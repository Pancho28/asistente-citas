from abc import ABC, abstractmethod
from typing import Any
from pydantic import BaseModel, field_validator


class InlineButton(BaseModel):
    text: str
    callback_data: str

    @field_validator("callback_data")
    @classmethod
    def validate_byte_length(cls, v: str) -> str:
        raw_bytes = v.encode("utf-8")
        if len(raw_bytes) > 64:
            raise ValueError(
                f"callback_data supera el límite de 64 bytes de Telegram ({len(raw_bytes)} bytes): '{v}'"
            )
        return v


def build_buttons_from_ui_hint(
    ui_hint: str | None, ui_data: dict[str, Any] | None
) -> list[list[InlineButton]]:
    """
    Convierte una pista visual de la herramienta (ui_hint) y sus datos asociados (ui_data)
    en una matriz de botones interactivos válidos (<= 64 bytes) para Telegram o WhatsApp.
    """
    if not ui_hint or not ui_data:
        return []

    hint = ui_hint.upper()

    # 1. Botones para agendamiento de horarios libres
    if hint == "SHOW_BOOKING_BUTTONS":
        prof_id = ui_data.get("profesional_id")
        prof_short = str(prof_id)[:8] if prof_id else "doc"
        raw_fecha = str(ui_data.get("fecha", ""))
        fecha_compact = raw_fecha.replace("-", "")

        horarios = ui_data.get("horarios_libres", [])
        buttons: list[InlineButton] = []
        for h in horarios:
            hora_compact = str(h).replace(":", "")
            cb = f"BOOK|{prof_short}|{fecha_compact}_{hora_compact}"
            buttons.append(InlineButton(text=f"🕐 {h}", callback_data=cb))

        # Agrupar en filas de 2 columnas
        grid: list[list[InlineButton]] = []
        for i in range(0, len(buttons), 2):
            grid.append(buttons[i : i + 2])
        return grid

    # 2. Botones de confirmación / cancelación de recordatorio
    elif hint == "SHOW_REMINDER_BUTTONS":
        cita_id = str(ui_data.get("cita_id", ""))
        return [
            [
                InlineButton(text="✅ Confirmar Asistencia", callback_data=f"R_OK|{cita_id}"),
                InlineButton(text="❌ Cancelar Cita", callback_data=f"R_CAN|{cita_id}"),
            ]
        ]

    # 3. Botones de acción para médicos (Cobrar / Inasistencia)
    elif hint == "SHOW_DOCTOR_ACTION_BUTTONS":
        citas = ui_data.get("citas", [])
        grid = []
        for c in citas:
            c_id = str(c.get("cita_id", ""))
            paciente_nombre = c.get("nombre_paciente", "Paciente")
            hora = c.get("hora_inicio", "")

            row = []
            if c.get("estado_financiero") != "PAGADO":
                row.append(
                    InlineButton(
                        text=f"💵 Cobrar ({hora})", callback_data=f"PAY_FULL|{c_id}"
                    )
                )
            row.append(
                InlineButton(
                    text=f"🚫 No Asistió ({hora})", callback_data=f"NO_SHOW|{c_id}"
                )
            )
            grid.append(row)
        return grid

    return []


class BaseMessagingAdapter(ABC):
    """Interfaz base para adaptadores de salida hacia plataformas de mensajería."""

    @abstractmethod
    async def send_message(
        self,
        recipient: str,
        text: str,
        buttons: list[list[InlineButton]] | None = None,
    ) -> bool:
        pass

    @abstractmethod
    async def answer_callback_query(
        self, callback_query_id: str, text: str | None = None
    ) -> bool:
        pass

