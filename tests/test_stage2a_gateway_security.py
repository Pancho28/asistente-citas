import hmac
import hashlib
import uuid
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession
from src.main import app
from src.schemas.context import UserContext, RoleEnum, ChannelOrigin
from src.schemas.webhook import parse_incoming_webhook, NormalizedWebhookEvent
from src.core.config import get_settings
from src.models.user import Usuario

settings = get_settings()

VALID_SECRET = settings.INTERNAL_GATEWAY_SECRET
TG_SECRET = settings.TELEGRAM_WEBHOOK_SECRET
WA_SECRET = settings.WHATSAPP_APP_SECRET
WA_VERIFY_TOKEN = settings.WHATSAPP_VERIFY_TOKEN

USER_UUID = str(uuid.uuid4())
HEADERS_VALID = {
    "X-Internal-Secret": VALID_SECRET,
    "X-User-Id": USER_UUID,
    "X-External-Chat-Id": "987654321",
    "X-Channel-Origin": "TELEGRAM",
    "X-User-Role": "PACIENTE",
    "X-User-Permissions": "disponibilidad:consultar,citas:agendar_propia",
}



@pytest.mark.asyncio
async def test_health_endpoint():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "timestamp" in data


@pytest.mark.asyncio
async def test_webhook_rejects_missing_or_invalid_secret():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. Sin cabecera X-Internal-Secret / X-Internal-Api-Key -> 401
        res_missing = await client.post("/api/v1/webhook", json={"test": 1})
        assert res_missing.status_code == 401
        assert res_missing.json()["code"] == "GATEWAY_AUTH_ERROR"

        # 2. Con cabecera errónea -> 401
        headers_bad = HEADERS_VALID.copy()
        headers_bad["X-Internal-Secret"] = "secreto_invalido"
        res_bad = await client.post("/api/v1/webhook", headers=headers_bad, json={"test": 1})
        assert res_bad.status_code == 401


@pytest.mark.asyncio
async def test_webhook_accepts_x_internal_api_key_header():
    """Verifica que el nombre oficial que inyecta api-gateway-auth (X-Internal-Api-Key) sea aceptado."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        headers_api_key = HEADERS_VALID.copy()
        del headers_api_key["X-Internal-Secret"]
        headers_api_key["X-Internal-Api-Key"] = VALID_SECRET

        payload = {
            "update_id": 9999,
            "message": {
                "message_id": 1,
                "from": {"id": 987654321, "first_name": "Francisco"},
                "text": "Hola gateway",
            },
        }
        res = await client.post("/api/v1/webhook", headers=headers_api_key, json=payload)
        assert res.status_code == 200
        assert res.json()["status"] == "accepted"


@pytest.mark.asyncio
async def test_webhook_rejects_missing_or_invalid_identity_headers_in_direct_mode():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Rol inválido -> 403
        bad_role_headers = HEADERS_VALID.copy()
        bad_role_headers["X-User-Role"] = "SUPER_HACKER"
        res_role = await client.post("/api/v1/webhook", headers=bad_role_headers, json={"test": 1})
        assert res_role.status_code == 403

        # Canal inválido -> 403
        bad_channel_headers = HEADERS_VALID.copy()
        bad_channel_headers["X-Channel-Origin"] = "DISCORD"
        res_channel = await client.post("/api/v1/webhook", headers=bad_channel_headers, json={"test": 1})
        assert res_channel.status_code == 403


@pytest.mark.asyncio
async def test_webhook_accepts_valid_headers_and_parses_context():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {
            "update_id": 10001,
            "message": {
                "message_id": 1,
                "from": {"id": 987654321, "first_name": "Francisco"},
                "chat": {"id": 987654321, "type": "private"},
                "text": "Hola asistente",
            },
        }
        res = await client.post("/api/v1/webhook", headers=HEADERS_VALID, json=payload)
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "accepted"
        assert data["user_id"] == USER_UUID
        assert data["role"] == "PACIENTE"
        assert data["event_type"] == "MESSAGE"


# =========================================================================
# Tests Unitarios de Validación Criptográfica Modular
# =========================================================================

def test_telegram_signature_verification():
    from src.core.webhook_signature import verify_telegram_webhook_signature
    from src.core.exceptions import GatewayAuthError

    # 1. Token válido
    verify_telegram_webhook_signature(TG_SECRET, TG_SECRET)

    # 2. Token ausente
    with pytest.raises(GatewayAuthError) as exc:
        verify_telegram_webhook_signature(None, TG_SECRET)
    assert exc.value.status_code == 401

    # 3. Token erróneo
    with pytest.raises(GatewayAuthError) as exc:
        verify_telegram_webhook_signature("token_falso", TG_SECRET)
    assert exc.value.status_code == 401


def test_whatsapp_signature_verification():
    from src.core.webhook_signature import verify_whatsapp_webhook_signature
    from src.core.exceptions import GatewayAuthError

    raw_body = b'{"entry":[{"changes":[{"value":{"messages":[{"from":"584121234567","text":{"body":"hola"}}]}}]}]}'
    valid_hash = hmac.new(WA_SECRET.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    valid_header = f"sha256={valid_hash}"

    # 1. Firma válida
    verify_whatsapp_webhook_signature(raw_body, valid_header, WA_SECRET)

    # 2. Cabecera ausente
    with pytest.raises(GatewayAuthError) as exc:
        verify_whatsapp_webhook_signature(raw_body, None, WA_SECRET)
    assert exc.value.status_code == 401

    # 3. Firma inválida
    with pytest.raises(GatewayAuthError) as exc:
        verify_whatsapp_webhook_signature(raw_body, "sha256=abcdef1234567890", WA_SECRET)
    assert exc.value.status_code == 401

    # 4. Formato sin prefijo sha256=
    with pytest.raises(GatewayAuthError) as exc:
        verify_whatsapp_webhook_signature(raw_body, valid_hash, WA_SECRET)
    assert exc.value.status_code == 401


def test_whatsapp_hub_challenge_verification():
    from src.core.webhook_signature import verify_whatsapp_hub_challenge
    from src.core.exceptions import GatewayAuthError

    # 1. Válido
    challenge = verify_whatsapp_hub_challenge("subscribe", WA_VERIFY_TOKEN, "challenge_123", WA_VERIFY_TOKEN)
    assert challenge == "challenge_123"

    # 2. Modo incorrecto
    with pytest.raises(GatewayAuthError) as exc:
        verify_whatsapp_hub_challenge("invalid_mode", WA_VERIFY_TOKEN, "challenge_123", WA_VERIFY_TOKEN)
    assert exc.value.status_code == 403

    # 3. Token incorrecto
    with pytest.raises(GatewayAuthError) as exc:
        verify_whatsapp_hub_challenge("subscribe", "wrong_token", "challenge_123", WA_VERIFY_TOKEN)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_meta_webhook_challenge_get_endpoint():
    """Verifica que el endpoint GET /api/v1/webhook responde con el challenge en texto plano."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get(
            "/api/v1/webhook",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": WA_VERIFY_TOKEN,
                "hub.challenge": "1158201444",
            },
        )
        assert res.status_code == 200
        assert res.text == "1158201444"


# =========================================================================
# Tests E2E de Modo Passthrough con Resolución de BD (Modo A)
# =========================================================================

@pytest.mark.asyncio
async def test_webhook_passthrough_telegram_creates_new_patient_in_db(async_session: AsyncSession):
    """
    Simula una llamada desde el Gateway por la ruta pública sin X-User-Id:
    Inyecta X-Internal-Api-Key y X-Telegram-Bot-Api-Secret-Token.
    Debe validar la firma, auto-registrar al usuario en BD como PACIENTE y retornar 200.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        unique_chat_id = f"tg_{uuid.uuid4().hex[:8]}"
        payload = {
            "update_id": 2001,
            "message": {
                "message_id": 1,
                "from": {"id": unique_chat_id, "first_name": "Paciente Passthrough"},
                "chat": {"id": unique_chat_id, "type": "private"},
                "text": "Hola, primera vez que escribo",
            },
        }
        headers = {
            "X-Internal-Api-Key": VALID_SECRET,
            "X-Telegram-Bot-Api-Secret-Token": TG_SECRET,
        }
        res = await client.post("/api/v1/webhook", headers=headers, json=payload)
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "accepted"
        assert data["role"] == "PACIENTE"
        assert data["channel"] == "TELEGRAM"
        assert uuid.UUID(data["user_id"])  # UUID válido generado en BD


@pytest.mark.asyncio
async def test_webhook_passthrough_whatsapp_resolves_existing_admin_and_view_mode(async_session: AsyncSession):
    """
    Simula una llamada de WhatsApp para un usuario existente con rol ADMIN y modo_vista_actual MEDICO.
    Debe verificar el HMAC-SHA256 del body, cargar el usuario de la BD y adoptar el rol efectivo MEDICO.
    """
    # 1. Crear el usuario ADMIN con vista MEDICO en la BD de pruebas
    admin_id = uuid.uuid4()
    admin_phone = f"58412{uuid.uuid4().hex[:7]}"
    admin = Usuario(
        id=admin_id,
        canal_origen="WHATSAPP",
        external_chat_id=admin_phone,
        nombre_completo="Doctor Admin",
        telefono=admin_phone,
        rol_sistema="ADMIN",
        modo_vista_actual="MEDICO",
        activo=True,
    )
    async_session.add(admin)
    await async_session.commit()

    raw_payload_dict = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "contacts": [{"profile": {"name": "Doctor Admin"}, "wa_id": admin_phone}],
                            "messages": [
                                {
                                    "from": admin_phone,
                                    "type": "text",
                                    "text": {"body": "Ver mi agenda de hoy"},
                                }
                            ],
                        }
                    }
                ]
            }
        ]
    }
    import json
    raw_body_bytes = json.dumps(raw_payload_dict).encode("utf-8")
    wa_signature = "sha256=" + hmac.new(WA_SECRET.encode("utf-8"), raw_body_bytes, hashlib.sha256).hexdigest()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        headers = {
            "Content-Type": "application/json",
            "X-Internal-Api-Key": VALID_SECRET,
            "X-Hub-Signature-256": wa_signature,
        }
        res = await client.post("/api/v1/webhook", headers=headers, content=raw_body_bytes)
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "accepted"
        assert data["user_id"] == str(admin_id)
        assert data["role"] == "MEDICO"  # Rol efectivo según modo_vista_actual
        assert data["channel"] == "WHATSAPP"


def test_normalized_webhook_event_telegram_message():
    raw_payload = {
        "update_id": 101,
        "message": {
            "message_id": 50,
            "from": {"id": 987654321, "first_name": "Francisco"},
            "text": "¿Qué citas tengo?",
        },
    }
    event = parse_incoming_webhook(raw_payload, ChannelOrigin.TELEGRAM)
    assert event.event_type == "MESSAGE"
    assert event.text == "¿Qué citas tengo?"
    assert event.sender_name == "Francisco"
    assert event.callback_data is None


def test_normalized_webhook_event_telegram_callback():
    raw_payload = {
        "update_id": 102,
        "callback_query": {
            "id": "cb_12345",
            "from": {"id": 987654321, "first_name": "Francisco"},
            "data": "R_OK|c829104a-1122-3344-5566-77889900aabb",
        },
    }
    event = parse_incoming_webhook(raw_payload, ChannelOrigin.TELEGRAM)
    assert event.event_type == "CALLBACK"
    assert event.callback_data == "R_OK|c829104a-1122-3344-5566-77889900aabb"
    assert event.callback_query_id == "cb_12345"
    assert event.sender_name == "Francisco"


def test_normalized_webhook_event_whatsapp_text():
    raw_payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "from": "584121234567",
                                    "type": "text",
                                    "text": {"body": "Buenas tardes, quiero agendar"},
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }
    event = parse_incoming_webhook(raw_payload, ChannelOrigin.WHATSAPP)
    assert event.event_type == "MESSAGE"
    assert event.text == "Buenas tardes, quiero agendar"


def test_normalized_webhook_event_whatsapp_interactive_button():
    raw_payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "from": "584121234567",
                                    "type": "interactive",
                                    "interactive": {
                                        "button_reply": {
                                            "id": "BOOK|prof_123|20261110_1000",
                                            "title": "10:00 AM",
                                        }
                                    },
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }
    event = parse_incoming_webhook(raw_payload, ChannelOrigin.WHATSAPP)
    assert event.event_type == "CALLBACK"
    assert event.callback_data == "BOOK|prof_123|20261110_1000"


def test_user_context_permissions_check():
    ctx = UserContext(
        user_id=uuid.uuid4(),
        external_chat_id="999",
        channel=ChannelOrigin.TELEGRAM,
        role=RoleEnum.PACIENTE,
        permissions=frozenset(["disponibilidad:consultar", "citas:agendar_propia"]),
    )
    assert ctx.has_permission("disponibilidad:consultar") is True
    assert ctx.has_permission("finanzas:registrar_pago") is False
    raw_payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "from": "584121234567",
                                    "type": "interactive",
                                    "interactive": {
                                        "button_reply": {
                                            "id": "BOOK|prof_123|20261110_1000",
                                            "title": "10:00 AM",
                                        }
                                    },
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }
    event = parse_incoming_webhook(raw_payload, ChannelOrigin.WHATSAPP)
    assert event.event_type == "CALLBACK"
    assert event.callback_data == "BOOK|prof_123|20261110_1000"


def test_user_context_permissions_check():
    ctx = UserContext(
        user_id=uuid.uuid4(),
        external_chat_id="999",
        channel=ChannelOrigin.TELEGRAM,
        role=RoleEnum.PACIENTE,
        permissions=frozenset(["disponibilidad:consultar", "citas:agendar_propia"]),
    )
    assert ctx.has_permission("disponibilidad:consultar") is True
    assert ctx.has_permission("finanzas:registrar_pago") is False

