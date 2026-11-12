import json
from uuid import UUID
from typing import Any
import redis.asyncio as redis
from src.core.config import get_settings

settings = get_settings()


async def get_redis_client() -> redis.Redis:
    return redis.from_url(settings.REDIS_URL, decode_responses=True)


async def acquire_slot_lock(
    redis_client: Any,
    prof_id: UUID,
    slot_str: str,
    user_id: UUID,
    ttl: int = 300
) -> bool:
    """
    Intenta adquirir un bloqueo atómico para el slot del profesional.
    Es reentrante si el mismo user_id ya posee el lock.
    """
    key = f"lock:slot:{prof_id}:{slot_str}"
    current_holder = await redis_client.get(key)
    if current_holder is not None:
        return current_holder == str(user_id)

    # Bloqueo atómico NX con expiración
    result = await redis_client.set(key, str(user_id), nx=True, ex=ttl)
    return bool(result)


async def release_slot_lock(
    redis_client: Any,
    prof_id: UUID,
    slot_str: str,
    user_id: UUID
) -> None:
    """
    Libera el bloqueo atómico solo si pertenece al user_id actual.
    """
    key = f"lock:slot:{prof_id}:{slot_str}"
    current_holder = await redis_client.get(key)
    if current_holder == str(user_id):
        await redis_client.delete(key)


async def append_chat_history(
    redis_client: Any,
    user_id: UUID,
    role: str,
    content: str,
    max_len: int = 10,
    ttl: int = 7200
) -> None:
    """
    Agrega un mensaje al historial de chat en Redis con ventana deslizante y TTL.
    """
    key = f"chat:history:{user_id}"
    payload = json.dumps({"role": role, "content": content})
    await redis_client.rpush(key, payload)
    await redis_client.ltrim(key, -max_len, -1)
    await redis_client.expire(key, ttl)


async def get_chat_history(
    redis_client: Any,
    user_id: UUID
) -> list[dict[str, Any]]:
    """
    Recupera los mensajes almacenados en la ventana de chat del usuario.
    """
    key = f"chat:history:{user_id}"
    items = await redis_client.lrange(key, 0, -1)
    history = []
    for item in items:
        try:
            history.append(json.loads(item))
        except (json.JSONDecodeError, TypeError):
            continue
    return history

