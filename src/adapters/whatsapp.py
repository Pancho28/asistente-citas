import logging
import httpx
from src.core.config import get_settings
from src.adapters.base import BaseMessagingAdapter, InlineButton

logger = logging.getLogger(__name__)
settings = get_settings()


class WhatsAppAdapter(BaseMessagingAdapter):
    """
    Adaptador de salida para Meta Cloud API (WhatsApp Business Platform).
    Soporta mensajes de texto y mensajes interactivos de botones (hasta 3 botones por mensaje).
    """

    def __init__(
        self,
        access_token: str | None = None,
        phone_number_id: str | None = None,
    ):
        self.access_token = access_token or settings.WHATSAPP_ACCESS_TOKEN
        self.phone_number_id = phone_number_id or settings.WHATSAPP_PHONE_NUMBER_ID
        self.base_url = f"https://graph.facebook.com/v21.0/{self.phone_number_id}/messages"

    async def send_message(
        self,
        recipient: str | None = None,
        text: str = "",
        buttons: list[list[InlineButton]] | None = None,
        phone_number: str | None = None,
    ) -> bool:
        target_phone = recipient or phone_number
        if not target_phone:
            return False

        if not self.access_token or not self.phone_number_id or "opcional" in self.access_token:
            logger.info("[WHATSAPP SIMULADO] Mensaje para %s: %s", target_phone, text)
            return True

        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

        # Formatear como interactivo si hay botones (Meta admite máx 3 botones)
        if buttons:
            flat_buttons = [btn for row in buttons for btn in row][:3]
            payload = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": phone_number,
                "type": "interactive",
                "interactive": {
                    "type": "button",
                    "body": {"text": text},
                    "action": {
                        "buttons": [
                            {
                                "type": "reply",
                                "reply": {
                                    "id": btn.callback_data,
                                    "title": btn.text[:20],
                                },
                            }
                            for btn in flat_buttons
                        ]
                    },
                },
            }
        else:
            payload = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": phone_number,
                "type": "text",
                "text": {"preview_url": False, "body": text},
            }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.post(self.base_url, headers=headers, json=payload)
                if res.status_code not in (200, 201):
                    logger.error("[WHATSAPP] Error enviando mensaje a %s: %s", phone_number, res.text)
                    return False
                return True
        except Exception as exc:
            logger.error("[WHATSAPP] Excepción al comunicar con Meta Graph API: %s", exc)
            return False

    async def answer_callback_query(
        self, callback_query_id: str, text: str | None = None
    ) -> bool:
        # En WhatsApp los clics en botones no requieren handshake de respuesta a la app
        return True

