import json
from typing import Annotated
from uuid import UUID
from fastapi import Header, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from src.core.config import get_settings
from src.core.database import get_db
from src.core.security import verify_internal_secret, ROLE_PERMISSIONS_MAP
from src.core.webhook_signature import (
    verify_telegram_webhook_signature,
    verify_whatsapp_webhook_signature,
    detect_channel_and_extract_sender,
)
from src.core.exceptions import GatewayAuthError
from src.schemas.context import UserContext, RoleEnum, ChannelOrigin
from src.models.user import Usuario

settings = get_settings()


async def get_user_context(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    x_internal_api_key: Annotated[str | None, Header(alias="X-Internal-Api-Key")] = None,
    x_internal_secret: Annotated[str | None, Header(alias="X-Internal-Secret")] = None,
    x_user_id: Annotated[str | None, Header(alias="X-User-Id")] = None,
    x_external_chat_id: Annotated[str | None, Header(alias="X-External-Chat-Id")] = None,
    x_channel_origin: Annotated[str | None, Header(alias="X-Channel-Origin")] = None,
    x_user_role: Annotated[str | None, Header(alias="X-User-Role")] = None,
    x_user_permissions: Annotated[str | None, Header(alias="X-User-Permissions")] = None,
    x_telegram_secret: Annotated[str | None, Header(alias="X-Telegram-Bot-Api-Secret-Token")] = None,
    x_hub_signature: Annotated[str | None, Header(alias="X-Hub-Signature-256")] = None,
) -> UserContext:
    """
    Dependencia FastAPI con Doble Modalidad de Seguridad:
    - Modo B (Cabeceras Directas / Middleware / Tests): Si vienen las cabeceras de identidad
      pre-calculadas (X-User-Id, X-User-Role, etc.), construye y valida el UserContext en memoria.
    - Modo A (Passthrough desde api-gateway-auth): Si viene por public_paths sin X-User-Id,
      valida la firma criptográfica del proveedor (Telegram / WhatsApp), extrae la identidad del payload
      y resuelve (o auto-registra) al Usuario en PostgreSQL aplicando RBAC y modo_vista_actual.
    """
    # 1. Validar autenticación interna con el Gateway (401 si falta o es inválido)
    secret_header = x_internal_api_key or x_internal_secret
    verify_internal_secret(secret_header)

    # 2. Modo B: Identidad pre-resuelta por cabeceras (o petición explícita en pruebas directas)
    has_identity_headers = any([x_user_id, x_external_chat_id, x_channel_origin, x_user_role])
    has_webhook_signature = bool(x_telegram_secret or x_hub_signature)

    if has_identity_headers or not has_webhook_signature:
        if not (x_user_id and x_external_chat_id and x_channel_origin and x_user_role):
            raise GatewayAuthError(
                message="Missing required identity headers (X-User-Id, X-External-Chat-Id, X-Channel-Origin, X-User-Role)",
                status_code=403,
            )

        try:
            user_uuid = UUID(x_user_id)
        except (ValueError, TypeError):
            raise GatewayAuthError(message=f"Invalid UUID in X-User-Id: {x_user_id}", status_code=403)

        try:
            channel = ChannelOrigin(x_channel_origin.upper())
        except ValueError:
            raise GatewayAuthError(message=f"Invalid X-Channel-Origin: {x_channel_origin}", status_code=403)

        try:
            role = RoleEnum(x_user_role.upper())
        except ValueError:
            raise GatewayAuthError(message=f"Invalid X-User-Role: {x_user_role}", status_code=403)

        permissions: frozenset[str] = frozenset()
        if x_user_permissions:
            permissions = frozenset(
                perm.strip() for perm in x_user_permissions.split(",") if perm.strip()
            )
        else:
            permissions = ROLE_PERMISSIONS_MAP.get(role, frozenset())

        return UserContext(
            user_id=user_uuid,
            external_chat_id=x_external_chat_id,
            channel=channel,
            role=role,
            permissions=permissions,
        )

    # 3. Modo A: Passthrough desde el Gateway (public_paths)
    raw_body = await request.body()
    try:
        payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
    except Exception as exc:
        raise GatewayAuthError(message=f"Invalid JSON body: {exc}", status_code=400)

    # Detectar canal y extraer remitente
    channel, external_chat_id, sender_name = detect_channel_and_extract_sender(
        payload, channel_hint=x_channel_origin
    )

    # Validar firma criptográfica
    if channel == ChannelOrigin.TELEGRAM:
        verify_telegram_webhook_signature(x_telegram_secret, settings.TELEGRAM_WEBHOOK_SECRET)
    elif channel == ChannelOrigin.WHATSAPP:
        verify_whatsapp_webhook_signature(raw_body, x_hub_signature, settings.WHATSAPP_APP_SECRET)

    # Resolver identidad en base de datos PostgreSQL
    stmt = select(Usuario).where(
        Usuario.canal_origen == channel.value,
        Usuario.external_chat_id == external_chat_id,
    )
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if user is None:
        # Auto-registro transparente de nuevo paciente
        user = Usuario(
            canal_origen=channel.value,
            external_chat_id=external_chat_id,
            nombre_completo=sender_name or "Paciente",
            rol_sistema="PACIENTE",
            modo_vista_actual="PACIENTE",
            activo=True,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)

    # Determinar rol efectivo (respetando modo_vista_actual si el usuario es ADMIN en BD)
    if user.rol_sistema == "ADMIN" and user.modo_vista_actual:
        effective_role = RoleEnum(user.modo_vista_actual)
    else:
        effective_role = RoleEnum(user.rol_sistema)

    perms = set(ROLE_PERMISSIONS_MAP.get(effective_role, frozenset()))
    if user.rol_sistema == "ADMIN":
        perms.add("admin:cambiar_vista")

    return UserContext(
        user_id=user.id,
        external_chat_id=user.external_chat_id,
        channel=channel,
        role=effective_role,
        permissions=frozenset(perms),
    )
