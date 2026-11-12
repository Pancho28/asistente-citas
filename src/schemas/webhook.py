from typing import Literal, Any
from pydantic import BaseModel, ConfigDict
from src.schemas.context import ChannelOrigin


class NormalizedWebhookEvent(BaseModel):
    event_type: Literal["MESSAGE", "CALLBACK"]
    text: str | None = None
    callback_data: str | None = None
    callback_query_id: str | None = None
    sender_name: str | None = None
    raw_payload: dict[str, Any]

    model_config = ConfigDict(extra="ignore")


def parse_incoming_webhook(payload: dict[str, Any], channel: ChannelOrigin) -> NormalizedWebhookEvent:
    """
    Normaliza el payload recibido desde Telegram o WhatsApp a una estructura unificada.
    """
    if channel == ChannelOrigin.TELEGRAM:
        # 1. Caso Telegram Callback Query (clic en botón inline)
        if "callback_query" in payload:
            cb = payload["callback_query"]
            sender = cb.get("from", {})
            return NormalizedWebhookEvent(
                event_type="CALLBACK",
                callback_data=cb.get("data"),
                callback_query_id=str(cb.get("id")) if cb.get("id") else None,
                sender_name=sender.get("first_name"),
                raw_payload=payload,
            )

        # 2. Caso Telegram Message (texto o comando)
        if "message" in payload:
            msg = payload["message"]
            sender = msg.get("from", {})
            return NormalizedWebhookEvent(
                event_type="MESSAGE",
                text=msg.get("text"),
                sender_name=sender.get("first_name"),
                raw_payload=payload,
            )

    elif channel == ChannelOrigin.WHATSAPP:
        # Payload estándar de Meta Cloud API
        try:
            entry = payload.get("entry", [])[0]
            change = entry.get("changes", [])[0]
            val = change.get("value", {})
            messages = val.get("messages", [])
            if messages:
                msg = messages[0]
                msg_type = msg.get("type")

                # Clic en botón interactivo
                if msg_type == "interactive":
                    interactive = msg.get("interactive", {})
                    button_reply = interactive.get("button_reply") or interactive.get("list_reply") or {}
                    return NormalizedWebhookEvent(
                        event_type="CALLBACK",
                        callback_data=button_reply.get("id"),
                        raw_payload=payload,
                    )

                # Mensaje de texto libre
                if msg_type == "text":
                    return NormalizedWebhookEvent(
                        event_type="MESSAGE",
                        text=msg.get("text", {}).get("body"),
                        raw_payload=payload,
                    )
        except (IndexError, AttributeError, KeyError):
            pass

    # Fallback genérico para mensajes de texto
    return NormalizedWebhookEvent(
        event_type="MESSAGE",
        text=payload.get("text"),
        raw_payload=payload,
    )

