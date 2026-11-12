"""
Script interactivo para probar el flujo completo con Google Gemini Flash en vivo
sin enviar mensajes reales a Telegram ni a WhatsApp.

Permite interactuar como Paciente o como Médico, verificar el Function Calling
de las herramientas MCP con la base de datos real, el Ancla Temporal y la generación
de botones interactivos (ui_hint).

Uso:
    python -m debug.test_live_ai_chat
"""
import asyncio
import uuid
from decimal import Decimal
from datetime import time, timedelta
from sqlalchemy import select
from src.core.config import get_settings, now_tz
from src.core.database import async_session_factory
from src.core.redis import get_redis_client
from src.models.user import Usuario, PerfilProfesional
from src.models.schedule import HorarioBase
from src.schemas.context import UserContext, RoleEnum, ChannelOrigin
from src.agent.orchestrator import process_message_with_ai
from src.agent.fast_route import process_fast_route, try_process_slash_view_command
from src.adapters.base import build_buttons_from_ui_hint


async def ensure_sample_doctor(db) -> PerfilProfesional:
    """Verifica si existe al menos un médico con horarios para que Gemini pueda consultar disponibilidad."""
    res = await db.execute(select(PerfilProfesional).where(PerfilProfesional.acepta_nuevas_citas == True))
    perfil = res.scalars().first()
    if perfil:
        return perfil

    print("ℹ️ Creando médico de demostración en PostgreSQL ('Dr. Carlos Mendoza - Medicina General')...")
    medico_user = Usuario(
        id=uuid.uuid4(),
        canal_origen="TELEGRAM",
        external_chat_id="demo_doctor_1",
        nombre_completo="Dr. Carlos Mendoza",
        telefono="+584120000001",
        rol_sistema="MEDICO",
        modo_vista_actual="MEDICO",
        activo=True,
    )
    db.add(medico_user)
    await db.flush()

    perfil = PerfilProfesional(
        id=uuid.uuid4(),
        usuario_id=medico_user.id,
        especialidad="Medicina General",
        duracion_cita_minutos=30,
        tarifa_base_consulta=Decimal("35.00"),
        moneda="USD",
        min_horas_anticipacion_cancelar=2,
        acepta_nuevas_citas=True,
    )
    db.add(perfil)
    await db.flush()

    # Horario de Lunes a Viernes (0 a 4), de 08:00 a 13:00
    for dia in range(5):
        h = HorarioBase(
            profesional_id=perfil.id,
            dia_semana=dia,
            hora_inicio=time(8, 0),
            hora_fin=time(13, 0),
            activo=True,
        )
        db.add(h)

    await db.commit()
    print("   [OK] Médico y horarios base (Lun-Vie 08:00 - 13:00) creados con éxito.")
    return perfil


async def main():
    settings = get_settings()

    print("=" * 70)
    print("🤖 PRUEBA EN VIVO: ORQUESTADOR GEMINI FLASH + MCP TOOLS (Sin Salida a Red)")
    print("=" * 70)

    # 1. Validar GEMINI_API_KEY
    key = settings.GEMINI_API_KEY
    if not key or key.startswith("tu_api_key") or key in ("dummy-key-for-tests", "test-gemini-key"):
        print("\n⚠️  ATENCIÓN:")
        print("   Tu variable GEMINI_API_KEY en el archivo .env todavía tiene el valor de plantilla:")
        print(f"   GEMINI_API_KEY={key}")
        print("\n   👉 Por favor abre tu archivo .env y coloca tu API Key de Google AI Studio:")
        print("   GEMINI_API_KEY=AIzaSy...")
        print("   Luego vuelve a ejecutar este script.\n")
        return

    print(f"🔑 Gemini API Key detectada (primeros caracteres: {key[:8]}...)")
    print(f"🧠 Modelo configurado: {settings.GEMINI_MODEL}")
    print(f"⏰ Zona horaria activa: {settings.DEFAULT_TIMEZONE} (Hora actual: {now_tz().strftime('%Y-%m-%d %I:%M %p %A')})")

    # 2. Conexión a BD y Redis
    print("\n📦 Conectando a PostgreSQL y Redis locales...")
    try:
        redis = await get_redis_client()
        await redis.ping()
        print("   [OK] Redis conectado.")
    except Exception as exc:
        print(f"   ❌ Error conectando a Redis en {settings.REDIS_URL}: {exc}")
        print("   Asegúrate de tener el contenedor docker activo: docker compose up -d asistente-citas-redis")
        return

    async with async_session_factory() as db:
        try:
            await ensure_sample_doctor(db)
        except Exception as exc:
            print(f"   ❌ Error conectando a PostgreSQL en {settings.DATABASE_URL}: {exc}")
            print("   Asegúrate de tener el contenedor docker activo: docker compose up -d asistente-citas-postgres")
            return

        # 3. Identidad de prueba (Paciente)
        paciente_chat_id = "test_paciente_chat"
        res_user = await db.execute(select(Usuario).where(Usuario.external_chat_id == paciente_chat_id))
        paciente = res_user.scalars().first()
        if not paciente:
            paciente = Usuario(
                id=uuid.uuid4(),
                canal_origen="TELEGRAM",
                external_chat_id=paciente_chat_id,
                nombre_completo="Francisco (Paciente Demo)",
                telefono="+584121234567",
                rol_sistema="PACIENTE",
                modo_vista_actual="PACIENTE",
                activo=True,
            )
            db.add(paciente)
            await db.commit()

        user_ctx = UserContext(
            user_id=paciente.id,
            external_chat_id=paciente.external_chat_id,
            channel=ChannelOrigin.TELEGRAM,
            role=RoleEnum.PACIENTE,
            permissions={"citas:consultar_disponibilidad", "citas:agendar_propia", "citas:ver_propias", "citas:cancelar_reagendar_propia", "onboarding:canjear_codigo"},
            sender_name="Francisco",
        )

        print("\n" + "=" * 70)
        print("👤 SESIÓN INICIADA COMO:")
        print(f"   Nombre: {user_ctx.sender_name} | Rol: {user_ctx.role.value} | Canal: {user_ctx.channel.value}")
        print("=" * 70)
        print("💡 Instrucciones:")
        print("   - Escribe cualquier mensaje como paciente, por ejemplo:")
        print("     • 'Hola, ¿qué citas tienen disponibles para mañana?'")
        print("     • '¿Cuánto cuesta la consulta y qué especialidades hay?'")
        print("     • '¿Cuáles son mis citas activas?'")
        print("   - O ingresa un comando de botón rápido como: 'BOOK|doc|20261110_0900'")
        print("   - Escribe 'salir' para terminar.\n")

        while True:
            try:
                user_msg = input("\n🗣️  Tú: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nSaliendo...")
                break

            if not user_msg:
                continue
            if user_msg.lower() in ("salir", "exit", "quit"):
                print("¡Hasta luego!")
                break

            print("\n⏳ Procesando con Gemini Flash...")

            # 1. Semáforo Ruta Rápida (0 tokens)
            if user_msg.startswith(("BOOK|", "R_OK|", "R_CAN|", "PAY_FULL|", "NO_SHOW|")):
                print("⚡ [Ruta Rápida - 0 Tokens]")
                agent_res = await process_fast_route(user_ctx, user_msg, db, redis)
            elif user_msg.startswith("/modo_"):
                print("⚡ [Comando Rápido de Vista - 0 Tokens]")
                slash_res = await try_process_slash_view_command(user_ctx, user_msg, db)
                agent_res = slash_res if slash_res else await process_message_with_ai(user_ctx, user_msg, db, redis)
            else:
                # 2. Orquestador Gemini Flash
                try:
                    agent_res = await process_message_with_ai(user_ctx, user_msg, db, redis)
                except Exception as exc:
                    print(f"❌ Error en Gemini Flash: {exc}")
                    continue

            # 3. Mostrar respuesta del modelo
            print(f"\n🤖 Asistente:\n{agent_res.reply_text}\n")

            # 4. Mostrar botones interactivos si se generó ui_hint
            if agent_res.ui_hint:
                print(f"🎯 UI Hint generado: {agent_res.ui_hint}")
                buttons = build_buttons_from_ui_hint(agent_res.ui_hint, agent_res.ui_data)
                if buttons:
                    print("🔘 Botones Interactivos que vería el usuario:")
                    for row in buttons:
                        row_str = " | ".join([f"[{b.text} -> '{b.callback_data}']" for b in row])
                        print(f"   {row_str}")

            print("-" * 70)


if __name__ == "__main__":
    asyncio.run(main())

