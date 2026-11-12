# Asistente Médico de Citas (IA + Híbrido)

Microservicio backend para la gestión automatizada de citas médicas, cálculo de disponibilidad por excepción, recordatorios programados y control financiero sobre **Telegram** y **WhatsApp**.

Construido con **Python 3.11+**, **FastAPI**, **PostgreSQL (SQLAlchemy 2.0 Async)**, **Redis (`arq`)** y **Gemini Flash (`google-genai`)** bajo una arquitectura híbrida protegida detrás de un **API Gateway**.

---

## 1. Arquitectura de Operación

```text
[ Internet: Telegram / WhatsApp ]
                │
                ▼ (HTTPS Público)
┌───────────────┴─────────────────────────────────────────────────────────┐
│ Proyecto en Railway (Red Privada Interna: *.railway.internal)           │
│                                                                         │
│  [ API Gateway Central ]                                                │
│     │ (Valida firmas, resuelve identidad e inyecta X-User-Role / Id)    │
│     ▼                                                                   │
│  [ Microservicio Asistente Médico (Sin dominio público) ]               │
│     ├── Ruta Rápida (Botones / callback_query) ──► 0 tokens (<50ms)     │
│     └── Ruta IA (Texto libre) ──► Gemini Flash + Tools MCP por Rol      │
│     │                                                                   │
│     ├──► [ PostgreSQL (postgres.railway.internal) ]                     │
│     └──► [ Redis + Worker ARQ (redis.railway.internal) ]                │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Mapa de Documentación (Spec-Driven Development)

Este repositorio sigue una estructura estricta de **SDD + TDD**. Antes de modificar el código, consulta:

* **[`SPEC.md`](./SPEC.md):** Fuente de la verdad funcional, modelo Entidad-Relación (Mermaid), contrato de cabeceras del API Gateway, estructura de claves en Redis y formato de botones rápidos.
* **[`PLAN.md`](./PLAN.md):** Plan Maestro de Implementación dividido en 4 etapas incrementales (firmas exactas, contrato de red con el API Gateway, casos borde y diseño de tests TDD).
* **[`TODO.md`](./TODO.md):** Tablero de control de progreso vivo (checklist *Red ➔ Green ➔ Refactor* por etapa).
* **[`AGENTS.md`](./AGENT.md):** Reglas para asistentes de código (Flujo de Fase 1 Planificación con Opus/Argon vs. Fase 2 TDD con Flash/Sonnet, restricciones de librerías y uso obligatorio de la carpeta `debug/`).
* **[`skills/`](./skills/):** Especificación de las herramientas MCP divididas por rol:
  * [`skills/SKILLS.md`](./skills/SKILLS.md) — Reglas globales de seguridad (Doble Barrera e inyección de identidad).
  * [`skills/paciente_skills.md`](./skills/paciente_skills.md) — Herramientas del rol `PACIENTE`.
  * [`skills/medico_skills.md`](./skills/medico_skills.md) — Herramientas del rol `MEDICO` (agenda y finanzas).
  * [`skills/admin_skills.md`](./skills/admin_skills.md) — Herramientas del superusuario `ADMIN` (invitaciones y cambio de vista).

---

## 3. Variables de Entorno (`.env.example`)

Crea un archivo `.env` en la raíz del proyecto basándote en esta plantilla:

```ini
# Entorno y Zona Horaria (Crítico para Ancla Temporal de IA y Cron de las 12:00 PM)
ENVIRONMENT=development
DEFAULT_TIMEZONE=America/Caracas
# En local usa 8005 para no colisionar con el puerto 8000 del API Gateway Go
PORT=8005

# Base de Datos y Redis (En local usa 5433 y 6380 para no colisionar con los contenedores del API Gateway; en producción usa *.railway.internal)
DATABASE_URL=postgresql+asyncpg://postgres:password@127.0.0.1:5433/citas_medicas
REDIS_URL=redis://127.0.0.1:6380/0

# Seguridad Interna con el API Gateway (X-Internal-Api-Key inyectada por api-gateway-auth)
INTERNAL_GATEWAY_SECRET=clave_secreta_compartida_con_el_gateway

# Validación Criptográfica Modular de Webhooks (Passthrough desde el API Gateway)
TELEGRAM_WEBHOOK_SECRET=tu_secret_token_de_telegram_webhook
WHATSAPP_APP_SECRET=tu_app_secret_de_meta_para_hmac_sha256
WHATSAPP_VERIFY_TOKEN=tu_verify_token_para_hub_challenge

# Google Gemini API (SDK google-genai)
GEMINI_API_KEY=tu_api_key_de_google_ai_studio
GEMINI_MODEL=gemini-2.5-flash

# Canales de Mensajería (Adaptadores de salida)
TELEGRAM_BOT_TOKEN=123456789:ABCDEF_tu_token_de_botfather
WHATSAPP_ACCESS_TOKEN=opcional_para_meta_cloud_api
WHATSAPP_PHONE_NUMBER_ID=opcional_para_meta_cloud_api
```

---

## 4. Instalación y Ejecución en Local

### 4.1 Preparar el entorno e instalar dependencias
```bash
python -m venv .venv
source .venv/bin/activate  # En Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

### 4.2 Levantar PostgreSQL (5433) y Redis (6380) en local (Docker Compose)
Para el desarrollo local se utilizan contenedores oficiales de PostgreSQL 16 (`5433:5432`) y Redis 7 (`6380:6379`) con puertos aislados para que puedan correr en paralelo con `api-gateway-auth` (`5432`, `6379`, `8000`) sin colisiones:
```bash
docker compose up -d
```

### 4.3 Aplicar migraciones en PostgreSQL (Alembic)
```bash
alembic upgrade head
```

### 4.4 Levantar los servicios
El sistema consta de **dos procesos ligeros** que pueden ejecutarse en terminales separadas:

1. **Servidor API (FastAPI en puerto 8005):**
   ```bash
   uvicorn src.main:app --reload --host 0.0.0.0 --port 8005
   ```
2. **Worker de Recordatorios y Tareas Programadas (`arq` + Redis):**
   *(Se encarga del lote diario de recordatorios a pacientes a las 12:00 PM y del resumen horario configurable para médicos)*
   ```bash
   arq src.workers.tasks.WorkerSettings
   ```

---

## 5. Simulación Local del API Gateway (Pruebas sin Telegram)

Dado que el microservicio recibe el contexto de seguridad desde las cabeceras del Gateway, puedes probar tanto la **Ruta IA** como la **Ruta Rápida de Botones** en local con `curl`:

### Ejemplo A: Simular mensaje de texto de un Paciente (Pasa por Gemini Flash)
```bash
curl -X POST http://localhost:8000/api/v1/webhook \
  -H "Content-Type: application/json" \
  -H "X-Internal-Secret: clave_secreta_compartida_con_el_gateway" \
  -H "X-User-Id: 550e8400-e29b-41d4-a716-446655440000" \
  -H "X-External-Chat-Id: 987654321" \
  -H "X-Channel-Origin: TELEGRAM" \
  -H "X-User-Role: PACIENTE" \
  -H "X-User-Permissions: disponibilidad:consultar,citas:agendar_propia,citas:ver_propias" \
  -d '{
    "update_id": 10001,
    "message": {
      "message_id": 1,
      "from": {"id": 987654321, "first_name": "Francisco"},
      "chat": {"id": 987654321, "type": "private"},
      "text": "Hola, ¿qué horarios tienes disponibles para el próximo lunes por la mañana?"
    }
  }'
```

### Ejemplo B: Simular clic en botón de confirmación (Ruta Rápida - 0 tokens de IA)
```bash
curl -X POST http://localhost:8000/api/v1/webhook \
  -H "Content-Type: application/json" \
  -H "X-Internal-Secret: clave_secreta_compartida_con_el_gateway" \
  -H "X-User-Id: 550e8400-e29b-41d4-a716-446655440000" \
  -H "X-External-Chat-Id: 987654321" \
  -H "X-Channel-Origin: TELEGRAM" \
  -H "X-User-Role: PACIENTE" \
  -d '{
    "update_id": 10002,
    "callback_query": {
      "id": "cb_999",
      "from": {"id": 987654321, "first_name": "Francisco"},
      "data": "R_OK|c829104a-1122-3344-5566-77889900aabb"
    }
  }'
```

---

## 6. Despliegue en Producción (Railway Private Networking)

Para minimizar costos de ancho de banda (*Egress*) y garantizar aislamiento de red, este servicio se despliega **dentro del mismo proyecto de Railway donde viven el API Gateway, PostgreSQL y Redis**:

1. **Sin Dominio Público:** No generes un dominio público para este microservicio en Railway. Solo el **API Gateway** está expuesto a internet (`POST /webhooks/{provider}/{app_id}`).
2. **Enrutamiento Interno desde el Gateway:** Configura el Gateway para hacer proxy hacia la dirección interna de Railway:
   ```text
   [http://asistente-citas.railway.internal:8000/api/v1/webhook](http://asistente-citas.railway.internal:8000/api/v1/webhook)
   ```
3. **Binding IPv6 para Red Privada de Railway:** La red interna `*.railway.internal` resuelve sobre IPv6. El comando de inicio en producción para el contenedor web debe escuchar en `::`:
   ```bash
   uvicorn src.main:app --host :: --port $PORT
   ```
4. **Servicios Nativos de Base de Datos y Redis (Sin Docker Compose en Producción):** En Railway se utilizan los servicios nativos administrados de **PostgreSQL** y **Redis** del proyecto. Conéctalos siempre mediante las variables internas `${{Postgres.DATABASE_PRIVATE_URL}}` y `${{Redis.REDIS_PRIVATE_URL}}` para evitar cargos por tráfico saliente (`src/core/config.py` normaliza automáticamente el prefijo `postgresql+asyncpg://`).

---

## 7. Pruebas Automatizadas (`tests/`) y Diagnóstico (`debug/`)

* **Ejecutar la suite formal de tests (TDD con Mocks, sin gasto de API):**
  ```bash
  pytest -v
  ```
* **Scripts de diagnóstico y resolución de errores (`debug/`):**
  Cualquier script rápido de prueba o reproducción de errores debe ubicarse en la carpeta `debug/` (ver reglas en `AGENTS.md`) y ejecutarse como módulo desde la raíz:
  ```bash
  python -m debug.test_script
  ```