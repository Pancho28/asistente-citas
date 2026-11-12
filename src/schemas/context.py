from enum import StrEnum
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class RoleEnum(StrEnum):
    ADMIN = "ADMIN"
    MEDICO = "MEDICO"
    PACIENTE = "PACIENTE"


class ChannelOrigin(StrEnum):
    TELEGRAM = "TELEGRAM"
    WHATSAPP = "WHATSAPP"


class UserContext(BaseModel):
    user_id: UUID
    external_chat_id: str
    channel: ChannelOrigin
    role: RoleEnum
    permissions: frozenset[str] = Field(default_factory=frozenset)

    model_config = ConfigDict(frozen=True)

    def has_permission(self, perm: str) -> bool:
        return perm in self.permissions

