import hmac
from src.core.config import get_settings
from src.core.exceptions import GatewayAuthError
from src.schemas.context import RoleEnum

settings = get_settings()

ROLE_PERMISSIONS_MAP: dict[RoleEnum, frozenset[str]] = {
    RoleEnum.PACIENTE: frozenset({
        "disponibilidad:consultar",
        "citas:agendar_propia",
        "citas:ver_propias",
        "citas:modificar_propia",
        "onboarding:canjear_codigo",
    }),
    RoleEnum.MEDICO: frozenset({
        "agenda:ver_pacientes_dia",
        "agenda:bloquear_horario",
        "agenda:configurar_perfil",
        "citas:gestion_manual_medico",
        "finanzas:registrar_pago",
        "finanzas:ver_reportes",
    }),
    RoleEnum.ADMIN: frozenset({
        "admin:generar_invitacion",
        "admin:gestionar_profesionales",
        "admin:cambiar_vista",
        "disponibilidad:consultar",
        "agenda:ver_pacientes_dia",
        "finanzas:ver_reportes",
    }),
}


def verify_internal_secret(secret_header: str | None) -> None:
    """
    Verifica que la cabecera X-Internal-Api-Key (o su alias X-Internal-Secret) coincida
    con la clave secreta compartida entre el API Gateway y este microservicio usando
    comparación de tiempo constante (hmac.compare_digest).
    """
    if not secret_header:
        raise GatewayAuthError(
            message="Missing X-Internal-Api-Key / X-Internal-Secret header",
            status_code=401,
        )

    expected = settings.INTERNAL_GATEWAY_SECRET
    if not hmac.compare_digest(secret_header.encode("utf-8"), expected.encode("utf-8")):
        raise GatewayAuthError(
            message="Invalid internal gateway secret",
            status_code=401,
        )
