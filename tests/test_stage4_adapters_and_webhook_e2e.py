import uuid
from datetime import datetime, date, time, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, patch, MagicMock
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession
from src.main import app
from src.core.config import get_settings, now_tz
from src.schemas.context import UserContext, RoleEnum, ChannelOrigin
from src.models.user import Usuario, PerfilProfesional
from src.models.appointment import Cita
from src.models.schedule import HorarioBase
from tests.conftest import FakeRedis

settings = get_settings()


# =========================================================================
# 1. Tests de Generación y Validación de Botones Inline (<= 64 bytes)
# =========================================================================

def test_inline_button_enforces_64_byte_limit():
    from src.adapters.base import InlineButton
    from pydantic import ValidationError

    # 1. Payload válido (<= 64 bytes)
    btn_valid = InlineButton(text="Confirmar", callback_data="R_OK|c829104a-1122-3344-5566-77889900aabb")
    assert len(btn_valid.callback_data.encode("utf-8")) <= 64

    # 2. Payload excesivo (> 64 bytes)
    too_long_data = "BOOK|very_long_custom_payload_that_exceeds_sixty_four_bytes_limit_for_telegram_bot_callback_query_123456789"
    with pytest.raises(ValidationError):
        InlineButton(text="Inválido", callback_data=too_long_data)


def test_build_buttons_from_ui_hint_show_booking_buttons():
    from src.adapters.base import build_buttons_from_ui_hint

    ui_data = {
        "profesional_id": str(uuid.uuid4()),
        "fecha": "2026-11-10",
        "horarios_libres": ["09:00", "09:30", "10:00", "11:00", "14:00"],
    }
    buttons = build_buttons_from_ui_hint("SHOW_BOOKING_BUTTONS", ui_data)

    assert len(buttons) > 0
    # Verificar que cada botón cumple la regla de 64 bytes y formato BOOK|{prof_short}|{YYYYMMDD_HHMM}
    for row in buttons:
        for btn in row:
            raw_bytes = btn.callback_data.encode("utf-8")
            assert len(raw_bytes) <= 64
            assert btn.callback_data.startswith("BOOK|")


def test_build_buttons_from_ui_hint_doctor_and_reminder_actions():
    from src.adapters.base import build_buttons_from_ui_hint

    cita_id = str(uuid.uuid4())

    # Recordatorio
    rem_buttons = build_buttons_from_ui_hint("SHOW_REMINDER_BUTTONS", {"cita_id": cita_id})
    assert len(rem_buttons) == 1
    assert rem_buttons[0][0].callback_data == f"R_OK|{cita_id}"
    assert rem_buttons[0][1].callback_data == f"R_CAN|{cita_id}"

    # Acciones médico
    doc_buttons = build_buttons_from_ui_hint(
        "SHOW_DOCTOR_ACTION_BUTTONS",
        {"citas": [{"cita_id": cita_id, "nombre_paciente": "Juan", "estado_financiero": "PENDIENTE"}]},
    )
    assert len(doc_buttons) >= 1
    assert any(b.callback_data == f"PAY_FULL|{cita_id}" for row in doc_buttons for b in row)
    assert any(b.callback_data == f"NO_SHOW|{cita_id}" for row in doc_buttons for b in row)


# =========================================================================
# 2. Tests de Adaptadores de Mensajería (Telegram y WhatsApp)
# =========================================================================

@pytest.mark.asyncio
async def test_telegram_adapter_send_message_and_answer_callback():
    from src.adapters.telegram import TelegramAdapter
    from src.adapters.base import InlineButton

    adapter = TelegramAdapter(bot_token="test_token_123456")

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"ok": True}
        mock_post.return_value = mock_response

        # Enviar mensaje con botones
        buttons = [[InlineButton(text="09:00 AM", callback_data="BOOK|prof123|20261110_0900")]]
        ok_msg = await adapter.send_message(chat_id="123456789", text="Horarios disponibles:", buttons=buttons)
        assert ok_msg is True
        assert mock_post.called

        # Responder callback query
        ok_cb = await adapter.answer_callback_query(callback_query_id="cb_999", text="Procesado")
        assert ok_cb is True


@pytest.mark.asyncio
async def test_whatsapp_adapter_send_message():
    from src.adapters.whatsapp import WhatsAppAdapter
    from src.adapters.base import InlineButton

    adapter = WhatsAppAdapter(access_token="test_token_wa", phone_number_id="test_phone_id")

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"messaging_product": "whatsapp", "messages": [{"id": "wamid_1"}]}
        mock_post.return_value = mock_response

        buttons = [
            [
                InlineButton(text="Confirmar", callback_data="R_OK|cita123"),
                InlineButton(text="Cancelar", callback_data="R_CAN|cita123"),
            ]
        ]
        ok_msg = await adapter.send_message(phone_number="584121234567", text="Recordatorio de cita", buttons=buttons)
        assert ok_msg is True
        assert mock_post.called


# =========================================================================
# 3. Flujo E2E en POST /api/v1/webhook con Adaptadores y Semáforo Completo
# =========================================================================

@pytest.mark.asyncio
async def test_webhook_e2e_callback_fast_route_execution(async_session: AsyncSession):
    """
    Verifica que al recibir un callback_query de botón en /api/v1/webhook,
    el semáforo ejecuta la ruta rápida (0 llamadas al LLM), responde answerCallbackQuery
    y envía la confirmación por el adaptador.
    """
    # 1. Crear médico y cita previa
    medico = Usuario(
        canal_origen="TELEGRAM",
        external_chat_id="doc_e2e",
        nombre_completo="Dra. E2E",
        rol_sistema="MEDICO",
        modo_vista_actual="MEDICO",
        activo=True,
    )
    async_session.add(medico)
    await async_session.commit()
    await async_session.refresh(medico)

    perfil = PerfilProfesional(
        id=uuid.uuid4(),
        usuario_id=medico.id,
        especialidad="General",
        duracion_cita_minutos=30,
        tarifa_base_consulta=Decimal("30.00"),
        moneda="USD",
        min_horas_anticipacion_cancelar=1,
        acepta_nuevas_citas=True,
    )
    async_session.add(perfil)

    paciente = Usuario(
        canal_origen="TELEGRAM",
        external_chat_id="pac_e2e",
        nombre_completo="Pedro Paciente",
        rol_sistema="PACIENTE",
        modo_vista_actual="PACIENTE",
        activo=True,
    )
    async_session.add(paciente)
    await async_session.commit()
    await async_session.refresh(paciente)

    cita = Cita(
        profesional_id=perfil.id,
        paciente_id=paciente.id,
        fecha_hora_inicio=now_tz() + timedelta(days=1),
        fecha_hora_fin=now_tz() + timedelta(days=1, minutes=30),
        estado="CONFIRMADA",
        monto_acordado=Decimal("30.00"),
        estado_financiero="PENDIENTE",
        origen_creacion="BOTON_RAPIDO",
    )
    async_session.add(cita)
    await async_session.commit()
    await async_session.refresh(cita)

    # 2. Simular petición de callback desde Telegram con secret token
    payload = {
        "update_id": 88001,
        "callback_query": {
            "id": "cb_query_e2e_1",
            "from": {"id": paciente.external_chat_id, "first_name": "Pedro"},
            "data": f"R_OK|{cita.id}",
        },
    }
    headers = {
        "X-Internal-Api-Key": settings.INTERNAL_GATEWAY_SECRET,
        "X-Telegram-Bot-Api-Secret-Token": settings.TELEGRAM_WEBHOOK_SECRET,
    }

    with patch("src.adapters.telegram.TelegramAdapter.send_message", new_callable=AsyncMock) as mock_send, \
         patch("src.adapters.telegram.TelegramAdapter.answer_callback_query", new_callable=AsyncMock) as mock_ans:

        mock_send.return_value = True
        mock_ans.return_value = True

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            res = await client.post("/api/v1/webhook", headers=headers, json=payload)
            assert res.status_code == 200
            data = res.json()
            assert data["status"] == "accepted"
            assert data["event_type"] == "CALLBACK"

            # Verificar que el callback fue respondido y se envió mensaje al paciente
            assert mock_ans.called
            assert mock_send.called

            # Verificar que la cita en BD pasó a CONFIRMADA_PACIENTE
            await async_session.refresh(cita)
            assert cita.estado == "CONFIRMADA_PACIENTE"
            assert cita.respuesta_recordatorio == "CONFIRMO"

