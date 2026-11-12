"""
Script de diagnóstico aislado (debug/) para validar la comunicación simulada
del API Gateway hacia este microservicio en /api/v1/webhook, tanto en Modo Directo (Modo B)
como en Modo Passthrough con validación criptográfica (Modo A), y opcionalmente
contra el API Gateway real si está levantado en http://127.0.0.1:8000.

Uso:
    python -m debug.test_gateway_bridge
"""
import asyncio
import hashlib
import hmac
import json
import uuid
import httpx
from httpx import AsyncClient, ASGITransport
from src.main import app
from src.core.config import get_settings

settings = get_settings()


async def run_gateway_bridge_simulation():
    print("=== SIMULADOR DE PUENTE: API GATEWAY -> MICROSERVICIO ===")
    user_id = str(uuid.uuid4())
    secret = settings.INTERNAL_GATEWAY_SECRET
    tg_secret = settings.TELEGRAM_WEBHOOK_SECRET
    wa_secret = settings.WHATSAPP_APP_SECRET
    wa_verify_token = settings.WHATSAPP_VERIFY_TOKEN

    common_headers = {
        "X-Internal-Api-Key": secret,
        "X-User-Id": user_id,
        "X-External-Chat-Id": "123456789",
        "X-Channel-Origin": "TELEGRAM",
        "X-User-Role": "PACIENTE",
        "X-User-Permissions": "disponibilidad:consultar,citas:agendar_propia",
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://internal") as client:
        # 1. Probar Healthcheck
        res_health = await client.get("/api/v1/health")
        print(f"1. GET /api/v1/health -> Status: {res_health.status_code}, Body: {res_health.json()}")
        assert res_health.status_code == 200

        # 2. Probar rechazo ante secreto incorrecto
        bad_headers = common_headers.copy()
        bad_headers["X-Internal-Api-Key"] = "secret_invalido"
        res_auth_fail = await client.post("/api/v1/webhook", headers=bad_headers, json={"test": 1})
        print(f"2. POST /api/v1/webhook con secreto inválido -> Status: {res_auth_fail.status_code} (Esperado: 401)")
        assert res_auth_fail.status_code == 401

        # 3. Simular reenvío de mensaje de texto de Telegram (Modo B - Cabeceras directas)
        telegram_message_payload = {
            "update_id": 90001,
            "message": {
                "message_id": 501,
                "from": {"id": 123456789, "first_name": "Francisco"},
                "chat": {"id": 123456789, "type": "private"},
                "text": "Hola, ¿tienes horas disponibles para mañana?",
            },
        }
        res_msg = await client.post("/api/v1/webhook", headers=common_headers, json=telegram_message_payload)
        print(f"3. POST /api/v1/webhook (Modo B - Mensaje texto) -> Status: {res_msg.status_code}, Event: {res_msg.json().get('event_type')}")
        assert res_msg.status_code == 200
        assert res_msg.json()["event_type"] == "MESSAGE"

        # 4. Simular reenvío de clic en botón de Telegram (callback_query)
        telegram_callback_payload = {
            "update_id": 90002,
            "callback_query": {
                "id": "cb_query_888",
                "from": {"id": 123456789, "first_name": "Francisco"},
                "data": "BOOK|prof_abc|20261110_0900",
            },
        }
        res_cb = await client.post("/api/v1/webhook", headers=common_headers, json=telegram_callback_payload)
        print(f"4. POST /api/v1/webhook (Modo B - Callback botón) -> Status: {res_cb.status_code}, Event: {res_cb.json().get('event_type')}")
        assert res_cb.status_code == 200
        assert res_cb.json()["event_type"] == "CALLBACK"

        # 5. Handshake Meta Webhook (GET /api/v1/webhook con hub.challenge)
        res_challenge = await client.get(
            "/api/v1/webhook",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": wa_verify_token,
                "hub.challenge": "987654321",
            },
        )
        print(f"5. GET /api/v1/webhook (Meta Challenge) -> Status: {res_challenge.status_code}, Body: {res_challenge.text}")
        assert res_challenge.status_code == 200
        assert res_challenge.text == "987654321"

        # 6. Modo Passthrough Telegram (Modo A - Sin X-User-Id, validando firma y resolviendo BD)
        # 6a. Usuario nuevo -> Auto-registro como PACIENTE
        new_patient_payload = {
            "update_id": 90003,
            "message": {
                "message_id": 1,
                "from": {"id": 888777666, "first_name": "Carlos Paciente"},
                "chat": {"id": 888777666, "type": "private"},
                "text": "Hola, primera vez que escribo",
            },
        }
        passthrough_tg_headers = {
            "X-Internal-Api-Key": secret,
            "X-Telegram-Bot-Api-Secret-Token": tg_secret,
        }
        res_tg_pass = await client.post("/api/v1/webhook", headers=passthrough_tg_headers, json=new_patient_payload)
        print(f"6a. POST /api/v1/webhook (Modo A - Paciente Nuevo) -> Status: {res_tg_pass.status_code}, Role: {res_tg_pass.json().get('role')}")
        assert res_tg_pass.status_code == 200
        assert res_tg_pass.json()["role"] == "PACIENTE"

        # 6b. Usuario Administrador Inicial pre-sembrado (123456789) -> Resuelve ADMIN
        res_admin_pass = await client.post("/api/v1/webhook", headers=passthrough_tg_headers, json=telegram_message_payload)
        print(f"6b. POST /api/v1/webhook (Modo A - Admin Sembrado) -> Status: {res_admin_pass.status_code}, Role: {res_admin_pass.json().get('role')}")
        assert res_admin_pass.status_code == 200
        assert res_admin_pass.json()["role"] == "ADMIN"

        # 7. Modo Passthrough WhatsApp (Modo A - HMAC-SHA256 sobre raw body)
        wa_payload = {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "contacts": [{"profile": {"name": "Usuario WA"}, "wa_id": "584129998877"}],
                                "messages": [
                                    {
                                        "from": "584129998877",
                                        "type": "text",
                                        "text": {"body": "Hola desde WhatsApp"},
                                    }
                                ],
                            }
                        }
                    ]
                }
            ]
        }
        wa_raw_bytes = json.dumps(wa_payload).encode("utf-8")
        wa_sig = "sha256=" + hmac.new(wa_secret.encode("utf-8"), wa_raw_bytes, hashlib.sha256).hexdigest()
        passthrough_wa_headers = {
            "Content-Type": "application/json",
            "X-Internal-Api-Key": secret,
            "X-Hub-Signature-256": wa_sig,
        }
        res_wa_pass = await client.post("/api/v1/webhook", headers=passthrough_wa_headers, content=wa_raw_bytes)
        print(f"7. POST /api/v1/webhook (Modo A - Passthrough WhatsApp) -> Status: {res_wa_pass.status_code}, Role: {res_wa_pass.json().get('role')}")
        assert res_wa_pass.status_code == 200

    print("\n[OK] Simulación de integración Gateway -> Microservicio (Modos A y B) completada exitosamente.")

    # 8. Opcional: Probar si el Gateway Go real está encendido en :8000
    print("\n--- Verificando si API Gateway real está escuchando en http://127.0.0.1:8000 ---")
    try:
        async with httpx.AsyncClient(timeout=2.0) as real_client:
            gw_res = await real_client.get("http://127.0.0.1:8000/health")
            print(f"   [INFO] API Gateway detectado en puerto 8000 -> Status: {gw_res.status_code}")
    except Exception:
        print("   [INFO] API Gateway no está corriendo en :8000 actualmente (normal si no se ha iniciado con 'go run' o docker compose).")


if __name__ == "__main__":
    asyncio.run(run_gateway_bridge_simulation())
