# AGENTS.md — Reglas de Ingeniería, Arquitectura y Flujo de Trabajo para Agentes de IA

## 1. Filosofía de Desarrollo: SDD + TDD + Optimización de Modelos
Este proyecto se construye bajo **Spec-Driven Development (SDD)** y **Test-Driven Development (TDD)**, dividiendo el trabajo en dos fases estrictas según la capacidad del modelo de IA utilizado en el IDE (Antigravity, Claude Code, Cline, Continue):

### Fase 1: Arquitectura y Planificación (Modelos de Alta Capacidad: Opus / Argon / Pro)
* **Objetivo:** Análisis profundo, diseño de algoritmos complejos, resolución de casos borde y creación de planes de implementación paso a paso.
* **Restricción de Tokens:** En esta fase **NO se escribe código de implementación masivo ni boilerplate**.
* **Entregable del Modelo:**
  1. Leer `SPEC.md` y el archivo correspondiente en `skills/`.
  2. Generar un plan de acción detallado (qué archivos crear/modificar, firmas de funciones exactas, casos de prueba a cubrir y posibles condiciones de carrera en Redis/PostgreSQL).
  3. Si el cambio requiere alterar el modelo de datos o los contratos de las herramientas, actualizar primero `SPEC.md` o `skills/*.md` antes de pasar a la Fase 2.

### Fase 2: Ejecución y Codificación TDD (Modelos Rápidos/Eficientes: Sonnet / Flash)
* **Objetivo:** Traducir el plan aprobado en la Fase 1 (`PLAN.md`) a código Python funcional y pruebas automatizadas con el menor consumo de tokens posible, llevando el control de avance en `TODO.md`.
* **Ciclo Estricto (Red ➔ Green ➔ Refactor):**
  1. **Ubicación:** Leer `TODO.md` para identificar la etapa activa y consultar únicamente la sección correspondiente en `PLAN.md`.
  2. **Red:** Escribir primero el test unitario o de integración en `tests/` basándose en las firmas definidas en `PLAN.md`.
  3. **Green:** Implementar el código mínimo necesario en `src/` siguiendo el plan al pie de la letra, sin re-arquitecturar ni inventar campos fuera de `SPEC.md`.
  4. **Refactor & Check:** Limpiar tipado, validar que todos los tests pasen y marcar las casillas completadas `[x]` en `TODO.md`.

---

## 2. Jerarquía de la Fuente de la Verdad
Antes de modificar o crear cualquier archivo en `src/`, el agente debe verificar los contratos en este orden:
1. **`SPEC.md`:** Fuente de la verdad para el modelo entidad-relación de PostgreSQL, claves de Redis, cabeceras del API Gateway y payloads de botones (`callback_query`).
2. **`skills/SKILLS.md` y `skills/*_skills.md`:** Fuente de la verdad para el catálogo de herramientas MCP expuestas a Gemini Flash, sus parámetros y sus permisos RBAC.
* **PROHIBIDO** añadir columnas a SQLAlchemy o argumentos a las Tools de Gemini que no estén documentados previamente en `SPEC.md` o `skills/`.

---

## 3. Regla Crítica de Debugging y Resolución de Errores (Carpeta `debug/`)
* Cuando el agente necesite aislar un error, probar una consulta SQL, verificar la conexión con Redis/PostgreSQL, ensayar el comportamiento de `google-genai` o generar cualquier script temporal de diagnóstico (como archivos `test.py` o `repro.py`), **está estrictamente obligado a crearlos dentro de la carpeta `debug/`** (ej. `debug/test_disponibilidad.py`, `debug/test_gemini_tools.py`).
* **PROHIBIDO** crear archivos `test.py` sueltos en la raíz del proyecto o mezclar scripts de diagnóstico rápido dentro de la carpeta formal `tests/`.

---

## 4. Stack Tecnológico y Reglas de Sintaxis (Python 3.11+)
El agente debe utilizar exclusivamente las versiones modernas de las librerías definidas a continuación, evitando sintaxis deprecada:

1. **FastAPI & Pydantic v2:**
   * Usar `model_validate()`, `model_dump()`, `ConfigDict(from_attributes=True)` y `Field()`.
   * **Prohibido** usar `.dict()`, `.parse_obj()` o `class Config:` de Pydantic v1.
2. **SQLAlchemy 2.0 (100% Asíncrono):**
   * Usar exclusivamente `AsyncSession`, `Mapped`, `mapped_column()` y consultas con `await session.execute(select(...))`.
   * **Prohibido** usar el estilo legado síncrono `session.query()`.
3. **SDK Oficial de Gemini (`google-genai`):**
   * Importar siempre desde el nuevo SDK oficial: `from google import genai` y `from google.genai import types`.
   * **Prohibido** importar o instalar la librería obsoleta `google-generativeai`.
4. **Redis & Tareas en Segundo Plano (`arq`):**
   * Usar `redis.asyncio` para el historial de chat y bloqueos atómicos (`SET NX EX 300`).
   * Usar `arq` para el cron diario de las `12:00 PM` (recordatorios a pacientes) y el heartbeat horario (resumen configurable del médico y cierre de citas pasadas).
5. **Manejo de Fechas y Zonas Horarias:**
   * Todas las columnas de fecha/hora en PostgreSQL son `TIMESTAMPTZ`.
   * Todo objeto `datetime` en Python debe ser *timezone-aware* (`datetime.now(tz=...)`). Prohibido usar `datetime.utcnow()` sin zona horaria.

---

## 5. Arquitectura de Capas: Separación `Services` vs. `MCP Tools` vs. `Fast Route`

```text
[ POST /api/v1/webhook ]
         │
         ├──► Ruta Rápida (Botones) : src/agent/fast_route.py ───────┐
         │                                                           ▼
         └──► Ruta IA (Texto Libre) : src/agent/mcp_tools/*.py ──► src/services/*.py ──► [ PostgreSQL / Redis ]
```

1. **Lógica de Negocio Única (`src/services/`):**
   * Toda consulta SQL, cálculo de disponibilidad, bloqueo en Redis o cálculo de saldos en `PAGOS` vive únicamente en `src/services/`.
2. **Herramientas MCP (`src/agent/mcp_tools/`):**
   * Las funciones en `patient.py`, `doctor.py` y `admin.py` son envoltorios (*wrappers*) delgados que validan permisos, inyectan el `user_id` del Gateway y llaman a `src/services/`.
   * **Prohibido** escribir sentencias SQL directas dentro de `src/agent/mcp_tools/` o `src/agent/fast_route.py`.
3. **Docstrings Orientados a Gemini Flash:**
   * El SDK `google-genai` lee los `docstrings` y *type hints* de las funciones en `src/agent/mcp_tools/`.
   * Cada función allí debe tener un `docstring` claro en español detallando qué hace, formato exacto de los parámetros (`YYYY-MM-DD`, `HH:MM`) y restricciones de negocio (ej. en `agendar_cita_paciente`, recordar explícitamente: *"NUNCA preguntes el motivo de consulta al paciente; envíalo solo si el usuario lo mencionó espontáneamente"*).

---

## 6. Reglas Inquebrantables de Seguridad (API Gateway & RBAC)
1. **Confianza Cero en el Payload de Mensajería:**
   * El usuario actual, su rol y sus permisos se leen **exclusivamente** de las cabeceras HTTP enviadas por el API Gateway (`X-User-Id`, `X-User-Role`, `X-User-Permissions`, `X-Channel-Origin`).
   * **Prohibido** confiar en IDs pasados como texto dentro del chat.
2. **Inyección Silenciosa de Identidad (Anti-IDOR):**
   * Las herramientas expuestas a Gemini **jamás** deben incluir `paciente_id`, `medico_id` ni `admin_id` en los parámetros que llena el LLM. Esos valores los inyecta el backend desde `X-User-Id`.
3. **Doble Barrera en MCP:**
   * **Barrera 1 (`registry.py`):** Filtrar la lista `tools=[...]` antes de llamar a Gemini según `X-User-Role`.
   * **Barrera 2 (Decorador):** Validar el permiso requerido antes de ejecutar la función en Python.
4. **Exclusividad de Cambio de Vista:**
   * Solo los usuarios cuyo `USUARIOS.rol_sistema == 'ADMIN'` pueden ejecutar el cambio de `modo_vista_actual` (`/modo_admin`, `/modo_medico`, `/modo_paciente`). Los roles `MEDICO` y `PACIENTE` tienen prohibido cambiar de vista.

---

## 7. Reglas para Pruebas Automatizadas (`tests/`)
* Ejecutar pruebas con `pytest` y `pytest-asyncio`.
* **Cero llamadas externas en tests:** Toda prueba en `tests/` debe hacer *mock* del cliente de Gemini (`google.genai.Client`) y de los adaptadores HTTP de Telegram/WhatsApp (`httpx.AsyncClient`). Ningún test puede consumir cuota real de la API de Gemini ni enviar mensajes reales al exterior.
* Priorizar tests sobre casos críticos de negocio:
  1. Colisión de citas simultáneas (verificación de locks en Redis e índice único parcial `idx_cita_unica_activa`).
  2. Cálculo de bloques disponibles