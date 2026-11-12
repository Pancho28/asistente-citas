from typing import Annotated, Any
from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession
from src.api.dependencies import get_user_context
from src.core.config import get_settings
from src.core.database import get_db
from src.core.redis import get_redis_client
from src.core.webhook_signature import verify_whatsapp_hub_challenge
from src.schemas.context import UserContext, ChannelOrigin
from src.schemas.webhook import parse_incoming_webhook
from src.adapters.base import build_buttons_from_ui_hint
from src.adapters.telegram import TelegramAdapter
from src.adapters.whatsapp import WhatsAppAdapter
from src.agent.fast_route import process_fast_route, try_process_slash_view_command, AgentResponse
from src.agent.orchestrator import process_message_with_ai

settings = get_settings()
router = APIRouter(prefix="/webhook", tags=["Webhook"])


@router.get("", response_class=PlainTextResponse)
async def verify_meta_webhook(
    mode: Annotated[str | None, Query(alias="hub.mode")] = None,
    verify_token: Annotated[str | None, Query(alias="hub.verify_token")] = None,
    challenge: Annotated[str | None, Query(alias="hub.challenge")] = None,
):
    """
    Handshake de verificación de Meta Cloud API para registrar el Webhook de WhatsApp.
    Devuelve el hub.challenge en texto plano si hub.mode == 'subscribe' y el token coincide.
    """
    challenge_val = verify_whatsapp_hub_challenge(
        mode=mode,
        verify_token=verify_token,
        challenge=challenge,
        expected_token=settings.WHATSAPP_VERIFY_TOKEN,
    )
    return PlainTextResponse(content=challenge_val)


@router.post("")
async def receive_webhook(
    ctx: Annotated[UserContext, Depends(get_user_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Any, Depends(get_redis_client)],
    payload: dict[str, Any],
):
    """
    Punto de entrada único para todos los eventos reenviados por el API Gateway.
    Semáforo de Entrada Híbrido:
    - Si es 'CALLBACK' (Botón): Ejecuta la Ruta Rápida en 0 tokens (<50ms).
    - Si es comando '/modo_*': Ejecuta cambio de vista exclusivo de ADMIN en 0 tokens.
    - Si es 'MESSAGE' (Texto libre): Invoca el Orquestador Gemini Flash con herramientas MCP filtradas por rol.
    - Despacha la respuesta interactiva formateada al usuario a través del adaptador correspondiente.
    """
    event = parse_incoming_webhook(payload, ctx.channel)
    adapter = TelegramAdapter() if ctx.channel == ChannelOrigin.TELEGRAM else WhatsAppAdapter()

    agent_response: AgentResponse

    # 1. Semáforo: Ruta Rápida de Botones
    if event.event_type == "CALLBACK" and event.callback_data:
        if event.callback_query_id:
            await adapter.answer_callback_query(event.callback_query_id)
        agent_response = await process_fast_route(ctx, event.callback_data, db, redis)

    # 2. Semáforo: Comandos rápidos de vista
    elif event.text and event.text.strip().startswith("/modo_"):
        slash_res = await try_process_slash_view_command(ctx, event.text, db)
        if slash_res is not None:
            agent_response = slash_res
        else:
            agent_response = await process_message_with_ai(ctx, event.text, db, redis)

    # 3. Semáforo: Ruta Inteligente IA (Gemini Flash)
    else:
        agent_response = await process_message_with_ai(ctx, event.text or "", db, redis)

    # 4. Construir botones interactivos si la herramienta generó ui_hint
    buttons = build_buttons_from_ui_hint(agent_response.ui_hint, agent_response.ui_data)

    # 5. Despachar mensaje de salida al usuario
    await adapter.send_message(
        recipient=ctx.external_chat_id,
        text=agent_response.reply_text,
        buttons=buttons if buttons else None,
    )

    return {
        "status": "accepted",
        "event_type": event.event_type,
        "user_id": str(ctx.user_id),
        "role": ctx.role.value,
        "channel": ctx.channel.value,
        "text": event.text,
        "callback_data": event.callback_data,
        "reply_text": agent_response.reply_text,
        "ui_hint": agent_response.ui_hint,
    }
