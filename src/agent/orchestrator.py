import json
import logging
import asyncio
import time as time_mod
from typing import Any
from google import genai
from google.genai import types
from sqlalchemy.ext.asyncio import AsyncSession
from src.core.config import get_settings, now_tz
from src.schemas.context import UserContext, RoleEnum
from src.agent.fast_route import AgentResponse
from src.agent.prompts import build_system_prompt
from src.services.availability_service import listar_catalogo_medicos_activos
from src.agent.mcp_tools.registry import (
    get_tools_for_user,
    current_user_ctx,
    current_db_session,
    current_redis_client,
)

logger = logging.getLogger(__name__)
settings = get_settings()


def _extract_text_from_response(response: Any) -> str:
    """
    Extrae limpiamente las partes de texto de una respuesta de Gemini,
    ignorando bloques internos de pensamiento (thought) o llamadas a funciones (function_call)
    para evitar warnings del SDK google-genai.
    """
    candidates = getattr(response, "candidates", None)
    if isinstance(candidates, (list, tuple)) and len(candidates) > 0:
        content = getattr(candidates[0], "content", None)
        parts = getattr(content, "parts", None)
        if isinstance(parts, (list, tuple)) and len(parts) > 0:
            text_chunks: list[str] = []
            for part in parts:
                if getattr(part, "thought", False) is True:
                    continue
                if getattr(part, "function_call", None) is not None:
                    continue
                part_text = getattr(part, "text", None)
                if isinstance(part_text, str) and part_text:
                    text_chunks.append(part_text)
            return "".join(text_chunks).strip()

    raw_text = getattr(response, "text", None)
    return raw_text if isinstance(raw_text, str) else ""


async def _generate_content_resilient(
    genai_client: Any,
    models: list[str],
    contents: Any,
    config: Any,
    hedged_timeout: float = 4.0,
    round_label: str = "initial",
) -> Any:
    """
    Ejecuta generate_content con Fast Failover y Hedged Requests escalonados:
    1. Si un modelo falla con 503/429/error, conmutación instantánea al siguiente modelo.
    2. Si el modelo primario tarda más de `hedged_timeout` (4s) sin responder, dispara
       en paralelo el siguiente modelo de respaldo y usa el primero que responda con éxito.
    """
    start_ts = time_mod.perf_counter()
    primary_model = models[0] if models else "unknown"
    last_exception: Exception | None = None
    remaining_models = list(models)
    active_tasks: dict[asyncio.Task, str] = {}

    while remaining_models or active_tasks:
        if not active_tasks and remaining_models:
            next_model = remaining_models.pop(0)
            task = asyncio.create_task(
                genai_client.aio.models.generate_content(
                    model=next_model,
                    contents=contents,
                    config=config,
                )
            )
            active_tasks[task] = next_model

        timeout_to_wait = hedged_timeout if remaining_models else None

        done, pending = await asyncio.wait(
            active_tasks.keys(),
            timeout=timeout_to_wait,
            return_when=asyncio.FIRST_COMPLETED,
        )

        if not done:
            if remaining_models:
                hedged_model = remaining_models.pop(0)
                logger.warning(
                    "[AI RESILIENCE] Tiempo de espera (%ss) superado. Disparando Hedged Request con modelo '%s'.",
                    hedged_timeout,
                    hedged_model,
                )
                h_task = asyncio.create_task(
                    genai_client.aio.models.generate_content(
                        model=hedged_model,
                        contents=contents,
                        config=config,
                    )
                )
                active_tasks[h_task] = hedged_model
            continue

        for completed_task in done:
            model_name = active_tasks.pop(completed_task)
            try:
                result = completed_task.result()
                for p_task in active_tasks:
                    p_task.cancel()
                latency_ms = int((time_mod.perf_counter() - start_ts) * 1000)
                failover_used = model_name != primary_model
                msg = (
                    f"[AI TELEMETRY] Respuesta generada por model='{model_name}' "
                    f"| round='{round_label}' | latency={latency_ms}ms | failover={failover_used}"
                )
                logger.info(msg)
                return result
            except Exception as exc:
                last_exception = exc
                logger.warning(
                    "[AI RESILIENCE] Modelo '%s' falló: %s. Modelos restantes: %s, Activos: %s",
                    model_name,
                    exc,
                    remaining_models,
                    list(active_tasks.values()),
                )

    if last_exception:
        raise last_exception
    raise RuntimeError("No models available for content generation")


async def process_message_with_ai(
    ctx: UserContext,
    text: str,
    db: AsyncSession,
    redis: Any,
    genai_client: genai.Client | None = None,
) -> AgentResponse:
    """
    Orquestador principal de Inteligencia Artificial (Gemini Flash + google-genai).
    1. Inyecta el Ancla Temporal Dinámica en el System Prompt.
    2. Aplica la Barrera 1 filtrando las herramientas MCP según el rol.
    3. Carga los últimos 10 mensajes desde Redis (chat:history:{user_id}).
    4. Resuelve function calling (incluyendo llamadas paralelas y encadenadas) capturando ui_hint y ui_data.
    5. Actualiza el historial en Redis con TTL 7200s (2 horas).
    """
    now = now_tz()
    catalogo_medicos = None
    if ctx.role == RoleEnum.PACIENTE:
        try:
            catalogo_medicos = await listar_catalogo_medicos_activos(db)
        except Exception as exc:
            logger.warning("[AI ORCHESTRATOR] Error obteniendo catálogo de médicos: %s", exc)

    system_instruction = build_system_prompt(ctx, now, catalogo_medicos=catalogo_medicos)
    tools = get_tools_for_user(ctx)

    # 1. Recuperar historial de chat desde Redis
    history_key = f"chat:history:{ctx.user_id}"
    raw_history = await redis.lrange(history_key, 0, -1)
    chat_history: list[dict[str, str]] = []
    for item in raw_history:
        try:
            chat_history.append(json.loads(item) if isinstance(item, str) else item)
        except Exception:
            continue

    # Limitar a los últimos 10 turnos
    chat_history = chat_history[-10:]

    # 2. Configurar cliente de Google GenAI
    if genai_client is None:
        if (
            settings.ENVIRONMENT == "test"
            or settings.GEMINI_API_KEY.startswith("tu_api_key")
            or settings.GEMINI_API_KEY in ("dummy-key-for-tests", "test-gemini-key")
        ):
            return AgentResponse(
                reply_text=f"Respuesta simulada de prueba para: {text}",
                ui_hint=None,
                ui_data=None,
            )
        genai_client = genai.Client(
            api_key=settings.GEMINI_API_KEY,
            http_options=types.HttpOptions(
                retry_options=types.HttpRetryOptions(attempts=1)
            ),
        )

    # 3. Establecer ContextVars de petición para inyección silenciosa Anti-IDOR
    t_user = current_user_ctx.set(ctx)
    t_db = current_db_session.set(db)
    t_redis = current_redis_client.set(redis)

    captured_ui_hint: str | None = None
    captured_ui_data: dict[str, Any] | None = None

    try:
        # Mapeo de herramientas por nombre para ejecución local
        tool_map = {func.__name__: func for func in tools}

        # Construir contenido del mensaje actual incorporando historial reciente
        contents: list[types.Content] = []
        for h in chat_history:
            role = "user" if h.get("role") == "user" else "model"
            contents.append(
                types.Content(
                    role=role,
                    parts=[types.Part.from_text(text=h.get("text", ""))],
                )
            )

        contents.append(
            types.Content(
                role="user",
                parts=[types.Part.from_text(text=text)],
            )
        )

        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            tools=tools if tools else None,
            temperature=0.3,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True) if tools else None,
        )

        current_contents = list(contents)
        try:
            response = await _generate_content_resilient(
                genai_client=genai_client,
                models=settings.gemini_models_list,
                contents=current_contents,
                config=config,
                hedged_timeout=settings.GEMINI_HEDGED_TIMEOUT_SECONDS,
                round_label="initial",
            )
        except Exception as exc:
            logger.error("[AI ORCHESTRATOR] Fallo en llamada inicial a Gemini: %s", exc)
            return AgentResponse(
                reply_text=(
                    "El servicio de inteligencia artificial está experimentando una alta demanda temporal. "
                    "Por favor, intenta de nuevo en unos momentos."
                ),
                ui_hint=None,
                ui_data=None,
            )

        # Bucle de resolución de Function Calling:
        # Soporta múltiples llamadas paralelas en un mismo turno (ej. consultar los 5 días de la semana)
        # y hasta 3 rondas encadenadas de herramientas.
        max_tool_rounds = 3
        round_idx = 0
        last_tool_messages: list[str] = []

        while response.function_calls and round_idx < max_tool_rounds:
            round_idx += 1
            function_response_parts: list[types.Part] = []

            for call in response.function_calls:
                func_name = call.name
                func_args = call.args or {}

                if func_name in tool_map:
                    tool_func = tool_map[func_name]
                    try:
                        tool_result = await tool_func(**func_args)
                    except Exception as exc:
                        logger.error("[MCP TOOL ERROR] Fallo en %s(%s): %s", func_name, func_args, exc)
                        tool_result = {
                            "success": False,
                            "code": "TOOL_EXECUTION_ERROR",
                            "message": str(exc),
                        }

                    if isinstance(tool_result, dict):
                        msg_val = tool_result.get("message") or tool_result.get("mensaje")
                        if isinstance(msg_val, str) and msg_val:
                            last_tool_messages.append(msg_val)

                    # Capturar ui_hint y ui_data (preferir resultados con horarios libres si hay múltiples llamadas)
                    if isinstance(tool_result, dict) and "ui_hint" in tool_result:
                        if (
                            captured_ui_data is None
                            or tool_result.get("horarios_libres")
                            or not captured_ui_data.get("horarios_libres")
                        ):
                            captured_ui_hint = tool_result["ui_hint"]
                            captured_ui_data = tool_result
                else:
                    tool_result = {
                        "success": False,
                        "code": "UNAUTHORIZED_TOOL",
                        "message": f"Herramienta '{func_name}' no autorizada o no encontrada.",
                    }

                function_response_parts.append(
                    types.Part.from_function_response(
                        name=func_name,
                        response={"result": tool_result},
                    )
                )

            # Preservar el turno del modelo con todos sus function_calls y thought_signatures íntegros
            candidates = getattr(response, "candidates", None)
            if (
                isinstance(candidates, (list, tuple))
                and len(candidates) > 0
                and isinstance(getattr(candidates[0], "content", None), types.Content)
            ):
                current_contents.append(candidates[0].content)
            else:
                model_parts = [
                    types.Part.from_function_call(name=c.name, args=c.args or {})
                    for c in response.function_calls
                ]
                current_contents.append(types.Content(role="model", parts=model_parts))

            # Añadir TODAS las respuestas de funciones juntas en un único turno 'user'
            current_contents.append(
                types.Content(
                    role="user",
                    parts=function_response_parts,
                )
            )

            # En la última ronda mantenemos la definición de tools pero fijamos mode='NONE'
            # para obligar a Gemini a responder en texto natural sin emitir más function_calls.
            is_last_round = round_idx >= max_tool_rounds
            next_config = types.GenerateContentConfig(
                system_instruction=system_instruction,
                tools=tools if tools else None,
                tool_config=(
                    types.ToolConfig(
                        function_calling_config=types.FunctionCallingConfig(mode="NONE")
                    )
                    if (tools and is_last_round)
                    else None
                ),
                temperature=0.3,
                automatic_function_calling=(
                    types.AutomaticFunctionCallingConfig(disable=True)
                    if tools
                    else None
                ),
            )

            try:
                response = await _generate_content_resilient(
                    genai_client=genai_client,
                    models=settings.gemini_models_list,
                    contents=current_contents,
                    config=next_config,
                    hedged_timeout=settings.GEMINI_HEDGED_TIMEOUT_SECONDS,
                    round_label="final_reply" if is_last_round else f"tool_round_{round_idx}",
                )
            except Exception as exc:
                logger.error("[AI ORCHESTRATOR] Fallo en turno subsiguiente de herramientas: %s", exc)
                return AgentResponse(
                    reply_text=(
                        "El servicio de inteligencia artificial está experimentando una alta demanda temporal. "
                        "Por favor, intenta de nuevo en unos momentos."
                    ),
                    ui_hint=captured_ui_hint,
                    ui_data=captured_ui_data,
                )

        fallback_text = " ".join(dict.fromkeys(last_tool_messages)) if last_tool_messages else "Solicitud procesada con éxito."
        reply_text = _extract_text_from_response(response) or fallback_text

        # 4. Guardar turno en Redis con TTL 7200s y ventana de 10 mensajes
        user_turn = json.dumps({"role": "user", "text": text})
        model_turn = json.dumps({"role": "model", "text": reply_text})

        await redis.rpush(history_key, user_turn)
        await redis.rpush(history_key, model_turn)
        await redis.ltrim(history_key, -10, -1)
        await redis.expire(history_key, 7200)

        return AgentResponse(
            reply_text=reply_text,
            ui_hint=captured_ui_hint,
            ui_data=captured_ui_data,
        )

    finally:
        current_user_ctx.reset(t_user)
        current_db_session.reset(t_db)
        current_redis_client.reset(t_redis)

