import hashlib
import hmac
from typing import Any
from src.core.exceptions import GatewayAuthError
from src.schemas.context import ChannelOrigin


def verify_telegram_webhook_signature(secret_header: str | None, expected_secret: str) -> None:
    """
    Verifica la cabecera X-Telegram-Bot-Api-Secret-Token configurada en el webhook de Telegram
    utilizando comparación de tiempo constante (hmac.compare_digest).
    """
    if not secret_header:
        raise GatewayAuthError(
            message="Missing X-Telegram-Bot-Api-Secret-Token header",
            status_code=401,
        )

    if not expected_secret or not hmac.compare_digest(
        secret_header.encode("utf-8"), expected_secret.encode("utf-8")
    ):
        raise GatewayAuthError(
            message="Invalid Telegram webhook secret token",
            status_code=401,
        )


def verify_whatsapp_webhook_signature(
    raw_body: bytes, signature_header: str | None, app_secret: str
) -> None:
    """
    Verifica la cabecera X-Hub-Signature-256 enviada por Meta Cloud API para WhatsApp Webhooks.
    Calcula el HMAC-SHA256 del cuerpo crudo de la petición usando el App Secret de Meta.
    """
    if not signature_header:
        raise GatewayAuthError(
            message="Missing X-Hub-Signature-256 header",
            status_code=401,
        )

    if not signature_header.startswith("sha256="):
        raise GatewayAuthError(
            message="Invalid X-Hub-Signature-256 format (must start with sha256=)",
            status_code=401,
        )

    if not app_secret:
        raise GatewayAuthError(
            message="WHATSAPP_APP_SECRET is not configured",
            status_code=401,
        )

    expected_signature = "sha256=" + hmac.new(
        app_secret.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(
        signature_header.encode("utf-8"), expected_signature.encode("utf-8")
    ):
        raise GatewayAuthError(
            message="Invalid WhatsApp X-Hub-Signature-256",
            status_code=401,
        )


def verify_whatsapp_hub_challenge(
    mode: str | None, verify_token: str | None, challenge: str | None, expected_token: str
) -> str:
    """
    Verifica el handshake GET (hub.challenge) de Meta Cloud API al configurar el webhook.
    Devuelve el challenge si mode=='subscribe' y el token coincide exactamente.
    """
    if mode != "subscribe":
        raise GatewayAuthError(
            message=f"Invalid hub.mode: {mode} (expected 'subscribe')",
            status_code=403,
        )

    if not verify_token or not expected_token or not hmac.compare_digest(
        verify_token.encode("utf-8"), expected_token.encode("utf-8")
    ):
        raise GatewayAuthError(
            message="Invalid hub.verify_token",
            status_code=403,
        )

    if not challenge:
        raise GatewayAuthError(
            message="Missing hub.challenge query parameter",
            status_code=400,
        )

    return challenge


def detect_channel_and_extract_sender(
    payload: dict[str, Any], channel_hint: str | None = None
) -> tuple[ChannelOrigin, str, str | None]:
    """
    Inspecciona el payload entrante para determinar si proviene de Telegram o WhatsApp
    y extrae (canal, external_chat_id, sender_name).
    """
    hint = channel_hint.upper() if channel_hint else None

    # Caso Telegram
    if hint == "TELEGRAM" or "message" in payload or "callback_query" in payload:
        if "callback_query" in payload:
            from_user = payload["callback_query"].get("from", {})
            sender_id = from_user.get("id")
            first_name = from_user.get("first_name")
            if not sender_id:
                raise GatewayAuthError(message="Missing 'from.id' in Telegram callback_query", status_code=400)
            return ChannelOrigin.TELEGRAM, str(sender_id), first_name

        if "message" in payload:
            from_user = payload["message"].get("from", {})
            sender_id = from_user.get("id")
            first_name = from_user.get("first_name")
            if not sender_id:
                raise GatewayAuthError(message="Missing 'from.id' in Telegram message", status_code=400)
            return ChannelOrigin.TELEGRAM, str(sender_id), first_name

        raise GatewayAuthError(message="Invalid Telegram update payload", status_code=400)

    # Caso WhatsApp (Meta Cloud API)
    if hint == "WHATSAPP" or "entry" in payload:
        try:
            entry = payload.get("entry", [])[0]
            change = entry.get("changes", [])[0]
            val = change.get("value", {})
            messages = val.get("messages", [])
            contacts = val.get("contacts", [])

            if not messages:
                raise GatewayAuthError(message="No messages array in WhatsApp webhook value", status_code=400)

            sender_id = messages[0].get("from")
            if not sender_id:
                raise GatewayAuthError(message="Missing 'from' in WhatsApp message", status_code=400)

            sender_name = contacts[0].get("profile", {}).get("name") if contacts else None
            return ChannelOrigin.WHATSAPP, str(sender_id), sender_name
        except (IndexError, KeyError, TypeError, AttributeError) as exc:
            raise GatewayAuthError(
                message=f"Failed to parse WhatsApp webhook payload: {exc}",
                status_code=400,
            )

    raise GatewayAuthError(
        message="Unrecognized webhook payload: cannot detect Telegram or WhatsApp structure",
        status_code=400,
    )

