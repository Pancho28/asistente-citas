class AppError(Exception):
    """Excepción base para errores de dominio y aplicación."""
    def __init__(self, message: str, code: str = "APP_ERROR", status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code


class GatewayAuthError(AppError):
    def __init__(self, message: str = "Error de autenticación con el API Gateway", status_code: int = 401):
        super().__init__(message=message, code="GATEWAY_AUTH_ERROR", status_code=status_code)


class PermissionDeniedError(AppError):
    def __init__(self, message: str = "Permiso denegado para ejecutar esta acción"):
        super().__init__(message=message, code="PERMISSION_DENIED", status_code=403)


class SlotTakenError(AppError):
    def __init__(self, message: str = "El horario seleccionado ya no está disponible"):
        super().__init__(message=message, code="SLOT_TAKEN", status_code=409)


class CancellationWindowExpiredError(AppError):
    def __init__(self, message: str = "El tiempo mínimo de anticipación para cancelar ha expirado"):
        super().__init__(message=message, code="CANCELLATION_WINDOW_EXPIRED", status_code=400)


class InvalidInvitationCodeError(AppError):
    def __init__(self, message: str = "El código de invitación es inválido, ya fue usado o ha expirado"):
        super().__init__(message=message, code="INVALID_INVITATION_CODE", status_code=400)


class ScheduleConflictError(AppError):
    def __init__(self, message: str = "Existe un conflicto con la agenda para el rango especificado"):
        super().__init__(message=message, code="SCHEDULE_CONFLICT", status_code=409)


class EntityNotFoundError(AppError):
    def __init__(self, entity_name: str = "Recurso"):
        super().__init__(message=f"{entity_name} no encontrado", code="NOT_FOUND", status_code=404)


class ValidationError(AppError):
    def __init__(self, message: str = "Error de validación en los datos proporcionados"):
        super().__init__(message=message, code="VALIDATION_ERROR", status_code=400)


