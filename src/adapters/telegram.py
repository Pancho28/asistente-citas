import logging
import httpx
from src.core.config import get_settings
from src.adapters.base import BaseMessagingAdapter, InlineButton

logger = logging.getLogger(__name__)
settings = get_settings()


class TelegramAdapter(BaseMessagingAdapter):
    """
    Adaptador de salida para Telegram Bot API.
    Envía mensajes de texto, teclados inline y responde callback queries.
    """

    def __init__(self, bot_token: str | None = None):
        self.bot_token = bot_token or settings.TELEGRAM_BOT_TOKEN
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}"

    async def send_message(
        self,
        recipient: str | None = None,
        text: str = "",
        buttons: list[list[InlineButton]] | None = None,
        chat_id: str | None = None,
    ) -> bool:
        target_chat = recipient or chat_id
        if not target_chat:
            return False

        if not self.bot_token or "tu_token" in self.bot_token:
            logger.info("[TELEGRAM SIMULADO] Mensaje para %s: %s", target_chat, text)
            return True

        url = f"{self.base_url}/sendMessage"
        payload = {
            "chat_id": target_chat,
            "text": text,
            "parse_mode": "HTML",
        }

        if buttons:
            inline_keyboard = [
                [{"text": btn.text, "callback_data": btn.callback_data} for btn in row]
                for row in buttons
            ]
            payload["reply_markup"] = {"inline_keyboard": inline_keyboard}

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.post(url, json=payload)
                if res.status_code != 200:
                    logger.error("[TELEGRAM] Error enviando mensaje a %s: %s", target_chat, res.text)
                    return False
                return True
        except Exception as exc:
            logger.error("[TELEGRAM] Excepción al comunicar con Telegram API: %s", exc)
            return False

    async def answer_callback_query(
        self, callback_query_id: str, text: str | None = None
    ) -> bool:
        if not self.bot_token or "tu_token" in self.bot_token:
            return True

        url = f"{self.base_url}/answerCallbackQuery"
        payload = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.post(url, json=payload)
                return res.status_code == 200
        except Exception as exc:
            logger.error("[TELEGRAM] Excepción en answerCallbackQuery: %s", exc)
            return False

