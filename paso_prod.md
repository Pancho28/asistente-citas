# 🚀 Guía Paso a Paso de Puesta en Producción (`paso_prod.md`)

Este documento detalla todos los pasos, comandos y configuraciones necesarias para desplegar el **Asistente Médico de Citas (IA + Híbrido)** en producción utilizando **Railway**, **Telegram Bot API** (y opcionalmente **WhatsApp Cloud API**) y **Google Gemini Flash**.

---

## 📋 Arquitectura de Despliegue en Producción

```text
[ Usuario en Telegram / WhatsApp ]
                │
                ▼ (HTTPS Público + Firma Criptográfica)
[ API Gateway (api-gateway-auth) ]  ◄─── Dominio Público (https://gateway.tudominio.com)
                │
                ▼ (Red Privada Interna de Railway: http://asistente-citas.railway.internal:8005)
                │ (Inyecta X-Internal-Api-Key + X-Telegram-Bot-Api-Secret-Token)
                │
[ Servicio 1: API Web (FastAPI) ]   ──┬──► [ PostgreSQL Nativo (Railway) ]
                                      ├──► [ Redis Nativo (Railway) ]
[ Servicio 2: Worker Cron (arq) ]   ──┘
```

---

## PASO 1: Configuración de Telegram (`@BotFather`) — ~3 minutos

### 1.1 Crear el Bot y obtener el Token
1. Abre Telegram y busca el usuario oficial verificado **`@BotFather`**.
2. Envía el comando `/newbot`.
3. Escribe el **nombre visible** de tu asistente (ej. `Asistente Médico de Citas`).
4. Escribe el **username** único terminado en `bot` (ej. `citas_medicas_ia_bot`).
5. `@BotFather` te entregará el token HTTP API. Guárdalo para las variables de entorno:
   - `TELEGRAM_BOT_TOKEN` = `7123456789:AAH...` (Al quitar el texto `"tu_token"`, el sistema desactiva automáticamente el Modo Simulado y envía mensajes reales a Telegram).
   - `TELEGRAM_BOT_USERNAME` = `citas_medicas_ia_bot` (sin el `@`).

### 1.2 Obtener tu `chat_id` real de Telegram para ser el Superusuario (`ADMIN`)
1. En Telegram, busca el bot **`@userinfobot`** (o `@RawDataBot`) y presiona **Iniciar / Start**.
2. Te responderá con tu número `Id` (ej. `543219876`).
3. Ese número lo pondrás en la variable `INITIAL_ADMIN_TELEGRAM_CHAT_ID`. Así, cuando Railway ejecute `alembic upgrade head` en el primer despliegue, la migración `0002_seed_initial_admin.py` te dará de alta automáticamente con `rol_sistema = 'ADMIN'`.

### 1.3 Generar tu Secreto de Firma para el Webhook
Genera una cadena aleatoria segura (de 32 a 64 caracteres alfanuméricos, por ejemplo `Secr3t_T3l3gr4m_W3bh00k_2026_Cl1n1c4`) y guárdala para:
- `TELEGRAM_WEBHOOK_SECRET`

---

## PASO 2: Configuración de Google Gemini API (AI Studio) — ~3 minutos

1. Ingresa a [Google AI Studio](https://aistudio.google.com/) y ve a **Get API key**.
2. Copia tu clave en `GEMINI_API_KEY`.
3. **Configuración de Resiliencia Multi-Modelo:**
   Mantén en tus variables de entorno la cadena de modelos Flash para que el sistema conmute en `<300ms` (Fast Failover) o a los `4.0s` (Hedged Request) si un modelo presenta picos de demanda (`503`):
   ```env
   IA_MODELS=["gemini-3.5-flash", "gemini-3.7-flash", "gemini-4-hydrogen", "gemini-3.6-flash", "gemini-3.5-flash-lite"]
   GEMINI_HEDGED_TIMEOUT_SECONDS=4.0
   ```
4. **Recomendación importante para Tráfico Real (Cuotas):**
   - El **Free Tier** de Google permite ~15 peticiones por minuto (RPM).
   - Para producción, vincula una cuenta de facturación en Google Cloud (**Pay-as-you-go / Tier 1**). Esto eleva el límite inmediatamente a **1,000+ RPM** y evita bloqueos `429 RESOURCE_EXHAUSTED` cuando varios pacientes consulten horarios al mismo tiempo.

---

## PASO 3: Despliegue en Railway (Base de Datos, Redis, API y Worker) — ~10 minutos

Debes crear los recursos **dentro del mismo Proyecto de Railway** donde corre tu `api-gateway-auth` para aprovechar la red privada interna (`*.railway.internal`).

### 3.1 Aprovisionar PostgreSQL y Redis Nativos
1. En el lienzo de tu proyecto en Railway, haz clic en **`+ New`** ➔ **`Database`** ➔ **`Add PostgreSQL`**.
2. Haz clic nuevamente en **`+ New`** ➔ **`Database`** ➔ **`Add Redis`**.

### 3.2 Crear el Servicio Principal (`asistente-citas-api`)
1. Haz clic en **`+ New`** ➔ **`GitHub Repo`** y selecciona el repositorio de `asistente-citas`.
2. En la pestaña **Settings** del servicio:
   - **Service Name:** `asistente-citas` (su dominio interno será `asistente-citas.railway.internal`).
   - **Networking (Private Networking):** Asegúrate de que esté habilitado. Si expones el webhook a través de `api-gateway-auth`, **no necesitas** generar un dominio público en `asistente-citas`; si deseas probarlo directo sin Gateway primero, puedes hacer clic en *Generate Domain*.
   - **Start Command / Config-as-Code (`railway.toml`):** El repositorio ya incluye [`railway.toml`](railway.toml) y [`entrypoint.sh`](entrypoint.sh), por lo que ejecutará automáticamente las migraciones de Alembic y levantará Uvicorn en `0.0.0.0:${PORT:-8005}` (`CMD ["/app/entrypoint.sh"]`). Si configuraste manualmente un *Custom Start Command* distinto en el panel, déjalo vacío o apunta al archivo `railway.toml`:
     ```bash
     /app/entrypoint.sh
     ```
   - **Healthcheck Path:** `/api/v1/health`

### 3.3 Configurar las Variables de Entorno en Railway
En la pestaña **Variables** (puedes usar *Raw Editor*), configura la siguiente plantilla exacta:

```env
# 1. Entorno y Zona Horaria
ENVIRONMENT=production
DEFAULT_TIMEZONE=America/Caracas
PORT=8005

# 2. Conexiones Nativas por Red Privada de Railway (El código convierte automáticamente postgres:// a postgresql+asyncpg://)
DATABASE_URL=${{Postgres.DATABASE_PRIVATE_URL}}
REDIS_URL=${{Redis.REDIS_PRIVATE_URL}}

# 3. Seguridad Interna con el API Gateway (Debe ser idéntica a la configurada en api-gateway-auth)
INTERNAL_GATEWAY_SECRET=tu_clave_secreta_compartida_con_el_gateway_produccion

# 4. Firmas Criptográficas de Webhooks
TELEGRAM_WEBHOOK_SECRET=Secr3t_T3l3gr4m_W3bh00k_2026_Cl1n1c4
WHATSAPP_APP_SECRET=tu_app_secret_de_meta_para_hmac_sha256
WHATSAPP_VERIFY_TOKEN=tu_verify_token_para_hub_challenge

# 5. Google Gemini Flash + Cadena de Resiliencia
GEMINI_API_KEY=tu_api_key_real_de_google_ai_studio
IA_MODELS=["gemini-3.5-flash", "gemini-3.7-flash", "gemini-4-hydrogen", "gemini-3.6-flash", "gemini-3.5-flash-lite"]
GEMINI_HEDGED_TIMEOUT_SECONDS=4.0

# 6. Credenciales Reales de Telegram
TELEGRAM_BOT_TOKEN=7123456789:AAH_tu_token_real_entregado_por_botfather
TELEGRAM_BOT_USERNAME=citas_medicas_ia_bot

# 7. Credenciales de WhatsApp Cloud API (Opcional: dejar con "opcional" si solo usarás Telegram inicialmente)
WHATSAPP_ACCESS_TOKEN=opcional_para_meta_cloud_api
WHATSAPP_PHONE_NUMBER_ID=opcional_para_meta_cloud_api

# 8. Superusuario Administrador Inicial (Seed automático en Alembic)
INITIAL_ADMIN_NOMBRE=Francisco
INITIAL_ADMIN_TELEGRAM_CHAT_ID=543219876
INITIAL_ADMIN_WHATSAPP_PHONE=+584121234567
```

### 3.4 Crear el Servicio Worker para Recordatorios y Resúmenes (`asistente-citas-worker`)
Para que se ejecuten el **Cron diario de las 12:00 PM** (recordatorios a pacientes con botones `[Confirmar]` / `[Cancelar]`) y el **Heartbeat horario** (cierre automático de citas pasadas y envío del resumen diario al médico):
1. En el mismo proyecto de Railway, haz clic en **`+ New`** ➔ **`GitHub Repo`** y vuelve a elegir `asistente-citas`.
2. En **Settings**:
   - **Service Name:** `asistente-citas-worker`
   - **Config File Path (Config-as-Code):** `/railway.worker.toml` (para que no herede el `startCommand` de la API en `railway.toml`)
   - **Custom Start Command:**
     ```bash
     arq src.workers.tasks.WorkerSettings
     ```
3. En **Variables**: Copia las mismas variables de entorno del servicio principal (puedes usar Shared Variables en Railway para no duplicarlas).

---

## PASO 4: Conectar el Webhook de Telegram con tu Servidor — ~2 minutos

Una vez que el servicio esté en estado **Active (Verde)** en Railway:

### 4.1 Registrar la URL del Webhook en Telegram
Abre Postman o tu terminal y ejecuta esta única petición `POST` reemplazando `<TU_TELEGRAM_BOT_TOKEN>`, `<URL_PUBLICA>` y `<TU_TELEGRAM_WEBHOOK_SECRET>`:

```bash
curl -X POST "https://api.telegram.org/bot<TU_TELEGRAM_BOT_TOKEN>/setWebhook" \
     -H "Content-Type: application/json" \
     -d '{
       "url": "https://<URL_PUBLICA_GATEWAY_O_SERVICIO>/api/v1/webhook",
       "secret_token": "Secr3t_T3l3gr4m_W3bh00k_2026_Cl1n1c4",
       "allowed_updates": ["message", "callback_query"]
     }'
```
*Respuesta esperada de Telegram:*
```json
{
  "ok": true,
  "result": true,
  "description": "Webhook was set"
}
```

> **Nota sobre `api-gateway-auth`:**  
> Si la URL pública apunta a tu **API Gateway**, asegúrate de que la ruta `/api/v1/webhook` esté declarada en `public_paths` del Gateway para que reenvíe el body intacto junto con la cabecera `X-Telegram-Bot-Api-Secret-Token` y añada `X-Internal-Api-Key`.

### 4.2 Verificar el Estado del Webhook en Telegram
Puedes comprobar en cualquier momento si Telegram tiene errores pendientes de entrega abriendo en tu navegador:
```text
https://api.telegram.org/bot<TU_TELEGRAM_BOT_TOKEN>/getWebhookInfo
```

---

## ✅ PASO 5: Checklist de Verificación Post-Despliegue (Smoke Test en Vivo)

1. **Verificar Salud del Servicio:**
   - Visitar `GET /api/v1/health` ➔ Debe responder `{"status": "ok", "database": "connected", "redis": "connected"}`.
2. **Prueba como Administrador (Tu cuenta de Telegram):**
   - Escríbele a tu bot en Telegram: *"Hola, genera un código de invitación para la especialidad de Cardiología"*.
   - El bot te reconocerá como `ADMIN` y te devolverá el código `MED-CARD-XXXX` y el link directo `https://t.me/tu_bot?start=MED-CARD-XXXX`.
3. **Prueba de Cambio de Vista (`/modo_medico` y `/modo_paciente`):**
   - Envía `/modo_paciente` desde tu Telegram y luego escribe `"Hola"` ➔ El bot te dará la bienvenida como paciente mostrándote el catálogo de médicos y especialidades activas.
   - Envía `/modo_admin` para volver a tu panel de control.
4. **Monitoreo de Telemetría en Railway:**
   - En la pestaña **Deploy Logs** de Railway verás en tiempo real qué modelo de Gemini está respondiendo cada mensaje y su latencia sin exponer datos sensibles:
     `[AI TELEMETRY] Respuesta generada por model='gemini-3.5-flash' | round='initial' | latency=680ms | failover=False`

