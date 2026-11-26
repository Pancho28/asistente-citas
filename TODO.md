# TODO.md — Tablero de Control y Progreso por Etapas (SDD + TDD)

> **Convención de Lectura Rápida (De lo más nuevo arriba a lo más antiguo abajo):**  
> Este documento está ordenado en **orden cronológico inverso**. Las tareas pendientes (`[ ]`) de la iteración actual están siempre al inicio del archivo para que el agente ejecutor solo necesite leer las primeras líneas hasta encontrar el primer bloque ya completado (`[x]`).
>
> **Estado Actual:** 🟢 **Desplegado en Producción en Railway (Bot Web + Worker ARQ + Par 2 + Etapas 10 y 11 Activos) | Pendiente: CI/CD**

---

## 📋 Tareas Pendientes (Iteración Actual & Infraestructura)
- [x] **Etapa 11 Completa — Suite de Administración y Operación SaaS en el Chatbot (`ADMIN`, completado el 2026-11-25 | `5/5` Etapa 11 PASS, `77/79` global):**
  - [x] **1. Gestión de Doctores por nombre y Ficha 360° (`listar_y_gestionar_profesionales`):** Resuelve médicos por nombre/especialidad sin exigir UUID y añade `accion="DETALLE"` (horario, zona horaria, onboarding pendiente y citas activas) en [`src/services/admin_service.py`](src/services/admin_service.py) y [`src/agent/mcp_tools/admin.py`](src/agent/mcp_tools/admin.py).
  - [x] **2. Edición administrativa y baja protegida de médicos:** Implementado `accion="EDITAR"` (nombre, teléfono, especialidad, zona horaria, tarifa, duración y horario) y `"DAR_DE_BAJA"` / `"REACTIVAR_CUENTA"` advirtiendo si existen citas futuras activas (`HAS_ACTIVE_APPOINTMENTS`).
  - [x] **3. Gestión de Códigos de Invitación (`gestionar_codigos_invitacion`):** Listado filtrado (`VIGENTES`, `USADOS`, `TODOS`) y revocación inmediata (`"REVOCAR"`).
  - [x] **4. Directorio y Bloqueo Efectivo de Pacientes (`gestionar_pacientes` + Webhook):** Búsqueda, historial/inasistencias, edición y bloqueo/reactivación (`USUARIOS.activo`), con cortafuegos en [`src/api/dependencies.py`](src/api/dependencies.py) y [`src/api/v1/webhook.py`](src/api/v1/webhook.py) rechazando botones e IA en 0 tokens (`blocked_inactive_user`).
  - [x] **5. Mesa de Ayuda / Soporte de Citas (`gestionar_citas_soporte`):** Consulta global, cancelación/reprogramación de citas y `"CANCELAR_JORNADA_MEDICO"` por emergencia creando `BloqueoAgenda` y notificando a los pacientes afectados.
  - [x] **6. Métricas Operativas y Financieras del SaaS (`consultar_metricas_plataforma`):** Médicos activos vs. con onboarding pendiente, pacientes nuevos/bloqueados, citas por estado, tasa de inasistencias e ingresos cobrados/proyectados (`HOY`, `SEMANA`, `MES`, `PERSONALIZADO`).
  - [x] **7. Comunicados Directos a Médicos (`enviar_comunicado_medicos`):** Envío directo por Telegram/WhatsApp a `"TODOS_ACTIVOS"`, `"ONBOARDING_PENDIENTE"` o a un médico puntual.
- [x] **Etapa 10 Completa — Notificaciones sin `Total: 0`, Huso Horario por Usuario, Resumen Semanal Filtrado y Onboarding Guiado Amable (Completado el 2026-11-25):**
  - [x] **Notificaciones sin pacientes:** Omitido `Total de pacientes programados: 0` en [`src/workers/tasks.py`](src/workers/tasks.py) cuando no hay citas (respondiendo únicamente `"No tienes citas programadas para esta jornada."`).
  - [x] **Huso horario por usuario/médico (`zona_horaria`):** Añadida columna `USUARIOS.zona_horaria` (migración `0003_timezone_and_onboarding`), helpers `normalize_timezone_name`, `now_tz(tz_name)`, `now_wall_utc` y `format_wall_time` en [`src/core/config.py`](src/core/config.py), y evaluación horaria por médico en `_do_heartbeat`.
  - [x] **Resumen semanal filtrado:** Añadido `construir_resumen_dias_habilitados` en [`src/services/appointment_service.py`](src/services/appointment_service.py), conectado a `ver_agenda_profesional` (`dias_habilitados_en_rango`) y regla en [`src/agent/prompts.py`](src/agent/prompts.py) excluyendo días fuera de horario.
  - [x] **Onboarding guiado amable del médico:** Añadida columna `PERFILES_PROFESIONALES.onboarding_campos_configurados`, tracker `obtener_estado_onboarding_medico` en [`src/services/onboarding_service.py`](src/services/onboarding_service.py) e inyección en [`src/agent/orchestrator.py`](src/agent/orchestrator.py) / [`src/agent/prompts.py`](src/agent/prompts.py) para recordar amablemente las 4 configuraciones pendientes (`horario_consulta`, `duracion_cita`, `tarifa_consulta`, `notificacion_diaria`).
  - [x] **TDD & Migración:** Nueva suite [`tests/test_stage10_notifications_timezone_weekly_and_onboarding.py`](tests/test_stage10_notifications_timezone_weekly_and_onboarding.py) (`4/4` PASS) y migración `alembic upgrade head` aplicada (`0003_timezone_and_onboarding (head)`).
- [x] **Despliegue Par 2 + Etapas 10 y 11 en Railway (Web + Worker ARQ — Completado el 2026-11-25):** Subidos los cambios de Par 2 (`DB_POOL_SIZE=5` / `DB_MAX_OVERFLOW=5` / `DB_POOL_TIMEOUT=15`), Etapa 10 (migración `0003`) y Etapa 11 (suite `ADMIN`), verificando `DATABASE_URL` (`citas_medicas`) y `REDIS_URL` (`/1`) en ambos servicios.
- [x] **Alineación Par 2 Compartido (`saas/PLAN.md` B.2, B.3 y Cutover Docker Raíz — Completado el 2026-11-25):**
  - [x] **B.2 — Pools de PostgreSQL:** Acotados `DB_POOL_SIZE=5`, `DB_MAX_OVERFLOW=5`, `DB_POOL_TIMEOUT=15` en [`src/core/config.py`](src/core/config.py) y `build_engine_options` en [`src/core/database.py`](src/core/database.py) (cubierto en [`tests/test_database_and_redis_pair2_config.py`](tests/test_database_and_redis_pair2_config.py)).
  - [x] **B.3 — Índice Redis `/1` y credenciales unificadas:** Actualizado default `REDIS_URL` en [`src/core/config.py`](src/core/config.py) a `redis://:redis_secure_password_2026@127.0.0.1:6380/1` junto con [`.env.example`](.env.example), `.env` y [`README.md`](README.md).
  - [x] **Fase 4 — Cutover Docker Raíz + Alembic:** Levantado [`saas/docker-compose.yml`](../docker-compose.yml) (`saas_shared_postgres` `:5433` + `saas_shared_redis` `:6380`), aplicadas migraciones hasta `0002_seed_initial_admin (head)` contra `citas_medicas`, verificada conectividad en vivo (`usuarios: 2`, Redis `/1 -> True`), retirado `asistente-citas/docker-compose.yml` y actualizado [`README.md`](README.md) hacia `../docker-compose.yml`.
- [ ] **CI/CD en GitHub Actions:** Crear workflow en `.github/workflows/ci.yml` que ejecute `pytest` automáticamente en cada `push` y `pull_request` hacia `main` antes de desplegar en Railway.
- [ ] *(Opcional Cross-Repo)* Programar en el repositorio del **API Gateway** el endpoint genérico `POST /webhooks/{provider}/{app_id}` según `SPEC.md` (actualmente ya funciona en producción mediante el prefijo `/asistente-citas/api/v1/webhook`).

---

## 💡 Mejoras a Futuro / Roadmap *(Por diseñar en `[PLAN]`)*
- [ ] **Perfil Coordinador / Administrador de Médicos (Agenda Multi-Médico)** *(Agregado: 2026-11-19)*: Nuevo rol intermedio de negocio (distinto del `ADMIN` técnico del bot) que permita a un usuario llevar la agenda, bloqueos, citas y pagos de **varios médicos asignados** (requerirá tabla de vínculo gestor-profesional y selección del médico activo en contexto antes de ejecutar tools MCP).
- [ ] **Integración con Google Calendar (Sincronización de Citas)** *(Agregado: 2026-11-19)*: Vincular la cuenta de Google Calendar de cada médico (OAuth 2.0 + refresh token cifrado) para crear, reagendar y cancelar eventos automáticamente en su calendario cuando cambie una cita (y opcionalmente consultar eventos externos como bloqueos al calcular disponibilidad).

---

## ✅ Historial Completado (De lo más reciente a lo más antiguo — Ya Verificado)

### 🚀 Despliegue en Producción en Railway y Ajustes Finales (Completado)
- [x] Aprovisionado en **Railway** PostgreSQL, Redis, el servicio Web (`asistente-citas` con `Dockerfile`, `entrypoint.sh` y `railway.toml`), el Worker (`asistente-citas-worker` con `railway.worker.toml`) y registrado el webhook en Telegram a través de `api-gateway-auth` siguiendo [`paso_prod.md`](./paso_prod.md).
- [x] Unificada la cadena de modelos en `IA_MODELS` (con `gemini-4-hydrogen` como 3.ª opción multi-familia), configurados logs limpios propios (`src.*`) en [`src/main.py`](src/main.py) y fijada `timezone = ZoneInfo(settings.DEFAULT_TIMEZONE)` en [`WorkerSettings`](src/workers/tasks.py) (**67/67 tests en verde**).

### 🔔 Etapa 9: Jornada Partida, Desambiguación de Citas y Notificaciones Cruzadas (Completada)
- [x] **1. Jornada partida en un mismo día (`hora_apertura_2` y `hora_cierre_2`):** Añadido segundo tramo horario diario en `configurar_horario_base_y_perfil` ([`src/services/onboarding_service.py`](src/services/onboarding_service.py) y [`src/agent/mcp_tools/doctor.py`](src/agent/mcp_tools/doctor.py)) y actualizado `_formatear_horario_semanal` en [`src/services/appointment_service.py`](src/services/appointment_service.py) para múltiples tramos.
- [x] **2. Desambiguación de homónimos o múltiples citas futuras:** Actualizado `resolver_cita_medico` ([`src/services/appointment_service.py`](src/services/appointment_service.py)) para lanzar `ValidationError` listando cada coincidencia y su `cita_id` cuando haya más de una cita activa en conflicto.
- [x] **3. Notificaciones proactivas cruzadas en tiempo real (Médico $\leftrightarrow$ Paciente):** Implementado `_notificar_usuario_directo` (*best-effort*) en [`src/services/appointment_service.py`](src/services/appointment_service.py) para avisar al médico cuando el paciente agende/cancele/reagende y al paciente cuando el médico mueva o cancele su cita manualmente.
- [x] **[TDD VERIFICADO]** Añadidos `test_doctor_split_shift_schedule_generation`, `test_doctor_disambiguation_on_homonym_patients`, `test_realtime_cross_notifications_between_doctor_and_patient` y cobertura MCP (**65/65 tests en verde**).

### 🗓️ Etapa 8: Desbloqueo de Agenda y Cierre de Días Laborales (Completada)
- [x] **1. Desbloquear un horario (`accion="DESBLOQUEAR"`):** Añadido soporte en `bloquear_horario_agenda` ([`src/services/appointment_service.py`](src/services/appointment_service.py) y [`src/agent/mcp_tools/doctor.py`](src/agent/mcp_tools/doctor.py)) para eliminar bloqueos que se solapen en un rango (`SLOT_UNBLOCKED` / `NO_BLOCKS_FOUND`) y reabrir la agenda.
- [x] **2. Cerrar/Desactivar días de la semana (`dias_cerrados`):** Añadido `dias_cerrados: list[int] | None = None` en `configurar_horario_base_y_perfil` ([`src/services/onboarding_service.py`](src/services/onboarding_service.py) y [`src/agent/mcp_tools/doctor.py`](src/agent/mcp_tools/doctor.py)) para desactivar días completos sin exigir horas de apertura/cierre.
- [x] **[TDD VERIFICADO]** Añadidos 3 nuevos tests en [`tests/test_stage2b_availability_and_appointments.py`](tests/test_stage2b_availability_and_appointments.py), [`tests/test_stage2b_finance_and_onboarding.py`](tests/test_stage2b_finance_and_onboarding.py) y [`tests/test_stage3_mcp_security_and_tools.py`](tests/test_stage3_mcp_security_and_tools.py) (**62/62 tests en verde**).

### 🩺 Etapa 7: Las 8 Mejoras Operativas del Día a Día (Médico y Paciente — Completada)
- [x] **1. Cancelación manual por el médico (`accion="CANCELAR"`):** Soportado `"CANCELAR"` en `agendar_o_mover_cita_manual_medico` ([`src/services/appointment_service.py`](src/services/appointment_service.py)) y `agendar_o_mover_cita_manual` ([`src/agent/mcp_tools/doctor.py`](src/agent/mcp_tools/doctor.py)).
- [x] **2. Resolución de citas por Nombre del Paciente (sin exigir UUID):** Implementado `resolver_cita_medico` en [`src/services/appointment_service.py`](src/services/appointment_service.py) y conectado en [`src/services/finance_service.py`](src/services/finance_service.py) (`registrar_pago_o_descuento`) y [`src/agent/mcp_tools/doctor.py`](src/agent/mcp_tools/doctor.py).
- [x] **3. Bloqueo de emergencia con `forzar_con_citas=True`:** Expuesto `forzar_con_citas: bool = False` en `bloquear_horario_agenda` ([`src/agent/mcp_tools/doctor.py`](src/agent/mcp_tools/doctor.py)).
- [x] **4. Prevención de colisiones en agenda manual del médico:** Implementado `_check_manual_slot_collision` y manejo de `IntegrityError` -> `SlotTakenError` en `agendar_o_mover_cita_manual_medico` y en el wrapper MCP.
- [x] **5. Horario incremental por día de la semana:** Actualizado `configurar_horario_base_y_perfil` ([`src/services/onboarding_service.py`](src/services/onboarding_service.py)) para reemplazar únicamente los días incluidos en `dias_semana`.
- [x] **6. Reagendamiento limpio del paciente:** Liberación de `release_slot_lock` en Redis para el horario previo y preservación de `paciente.nombre_completo` en `cancelar_o_reagendar_cita_paciente` (`accion="REAGENDAR"`).
- [x] **7. Cancelar/Reagendar cita propia sin exigir UUID:** Permitido `cita_id: str | None = None` en `cancelar_o_reagendar_cita_propia` ([`src/agent/mcp_tools/patient.py`](src/agent/mcp_tools/patient.py)) y resolución automática por próxima cita activa o fecha `YYYY-MM-DD` en [`src/services/appointment_service.py`](src/services/appointment_service.py).
- [x] **8. Validación de horario laboral (`HorarioBase`) al agendar:** Validación en `agendar_cita_paciente` contra `HorarioBase` activo del médico (`ValidationError` si está fuera de horario).
- [x] **[TDD VERIFICADO]** Añadidos 5 nuevos tests de integración/MCP en `tests/test_stage2b_*.py` y `tests/test_stage3_mcp_security_and_tools.py` (59/59 tests en verde).

### 🚀 Etapa 6: Catálogo Dinámico y Telemetría de Modelos (Completada)
- [x] **Mejora 10 — Telemetría de Modelo Ganador en Consola (`[AI TELEMETRY]`):**
  - Implementado en `_generate_content_resilient` ([`src/agent/orchestrator.py`](src/agent/orchestrator.py)) el log libre de PII:
    `[AI TELEMETRY] Respuesta generada por model='...' | round='...' | latency=...ms | failover=...`
- [x] **Mejora 9 — Inyección del Catálogo de Médicos y Especialidades al Saludar como Paciente:**
  - Implementado `listar_catalogo_medicos_activos(db)` en [`src/services/availability_service.py`](src/services/availability_service.py).
  - Actualizado `build_system_prompt` en [`src/agent/prompts.py`](src/agent/prompts.py) y `process_message_with_ai` en [`src/agent/orchestrator.py`](src/agent/orchestrator.py).
  - Verificado con 2 tests TDD en [`tests/test_stage3_fast_route_and_orchestrator.py`](tests/test_stage3_fast_route_and_orchestrator.py) (54/54 tests pasando).

---

### 🐛 Etapa 5: Registro de Bugs Encontrados en Pruebas en Vivo y su Resolución (Completada)
- [x] **Bug 8 — Cita de María agendada al médico incorrecto (`Dr. Carlos Mendoza` en lugar de `Dr. Juan Pérez`):**
  - **Causa:** El historial en Redis no preservaba el `profesional_id` retornado por `consultar_disponibilidad` en el turno previo, por lo que al agendar con `profesional_id=None` se tomaba al primer médico registrado (`perfiles[0]`).
  - **Resolución:** Memoria de contexto en Redis `chat:last_prof:{user_id}` (TTL 2h) en [`src/agent/mcp_tools/patient.py`](src/agent/mcp_tools/patient.py), fallback automático si `profesional_id` es `None`, y resolución flexible con `_normalize_text` en [`src/services/appointment_service.py`](src/services/appointment_service.py).
- [x] **Bug 7 — Alta Demanda / `503 UNAVAILABLE` en modelos Gemini Flash (Opción B: Fast Failover + Hedged Request a los 4s):**
  - **Causa:** Los modelos `gemini-3.x-flash` sufren picos de demanda y el retry interno del SDK (`tenacity`) reintentaba sobre el mismo modelo saturado durante 10–15s antes de fallar con 500.
  - **Resolución:** `types.HttpOptions(retry_options=types.HttpRetryOptions(attempts=1))`, cadena `GEMINI_FALLBACK_MODELS` como `list[str]`, y `_generate_content_resilient` en [`src/agent/orchestrator.py`](src/agent/orchestrator.py) con Fast Failover (<300ms) + Hedged Request a los 4.0s + degradación elegante.
- [x] **Bug 6 — Sensibilidad a tildes en `especialidad` (`"Cardiologia"` vs `"Cardiología"`), `UUID` estricto y `timezone` en bloqueos:**
  - **Resolución:** Normalización Unicode (`NFKD`) en [`src/services/availability_service.py`](src/services/availability_service.py), manejo seguro de excepciones y UUIDs en MCP tools, y normalización `timezone.utc` en [`src/services/appointment_service.py`](src/services/appointment_service.py).
- [x] **Bug 5 — `Warning: there are non-text parts in the response: ['function_call', 'function_call']` y `Error: socket hang up` en Postman:**
  - **Resolución:** Ejecución en lote de llamadas paralelas a herramientas en [`src/agent/orchestrator.py`](src/agent/orchestrator.py), envío de todos los `Part.from_function_response` en un único turno `role="user"`, y `_extract_text_from_response()`.
- [x] **Bug 4 — `400 INVALID_ARGUMENT: Function call is missing a thought_signature in functionCall parts`:**
  - **Resolución:** Preservación directa de `response.candidates[0].content` en [`src/agent/orchestrator.py`](src/agent/orchestrator.py).
- [x] **Bug 3 — `404 NOT_FOUND` con `gemini-2.5-flash` para cuentas nuevas:**
  - **Resolución:** Migración a la familia `gemini-3.x-flash`.
- [x] **Bug 2 — `[TELEGRAM] Error enviando mensaje a None: {"ok":false,"error_code":401,"description":"Unauthorized"}`:**
  - **Resolución:** Detección de tokens placeholder en [`src/adapters/telegram.py`](src/adapters/telegram.py) y [`src/adapters/whatsapp.py`](src/adapters/whatsapp.py) para operar en **Modo Simulado Local**.
- [x] **Bug 1 — `TypeError: WhatsAppAdapter.send_message() / TelegramAdapter.send_message() got an unexpected keyword argument 'recipient'`:**
  - **Resolución:** Ambos adaptadores aceptan `recipient: str | None = None` como primer parámetro manteniendo compatibilidad con `chat_id` y `phone_number`.

---

### 📡 Etapa 4: Adaptadores de Mensajería, Webhook E2E y Workers Programados (`arq`) (Completada)
- [x] **[RED - Tests]** Crear `tests/test_stage4_adapters_and_webhook_e2e.py` y `tests/test_stage4_arq_workers.py`.
- [x] **[GREEN - Código]** Implementar `src/adapters/base.py`, `src/adapters/telegram.py`, `src/adapters/whatsapp.py`, `src/api/v1/webhook.py` y `src/workers/tasks.py`.
- [x] **[REFACTOR & CHECK]** Suite de etapa 4 pasando al 100%.

---

### 🤖 Etapa 3: Capa de Agente IA (`google-genai`), Doble Barrera MCP y Ruta Rápida (`fast_route.py`) (Completada)
- [x] **[RED - Tests]** Crear `tests/test_stage3_mcp_security_and_tools.py` y `tests/test_stage3_fast_route_and_orchestrator.py`.
- [x] **[GREEN - Código]** Implementar `src/agent/mcp_tools/registry.py`, `patient.py`, `doctor.py`, `admin.py`, `src/agent/prompts.py`, `src/agent/fast_route.py` y `src/agent/orchestrator.py`.
- [x] **[REFACTOR & CHECK]** Suite de etapa 3 pasando al 100%.

---

### 🔐 Etapa 2: Integración con API Gateway y Capa de Servicios de Negocio (`src/services/`) (Completada)
- [x] **Parte 2B (Servicios de Negocio):** `tests/test_stage2b_availability_and_appointments.py`, `tests/test_stage2b_finance_and_onboarding.py`, `src/services/availability_service.py`, `appointment_service.py`, `finance_service.py`, `onboarding_service.py`.
- [x] **Parte 2A (Seguridad Modular y `UserContext`):** `tests/test_stage2a_gateway_security.py`, `src/schemas/context.py`, `src/schemas/webhook.py`, `src/core/webhook_signature.py`, `src/core/security.py`, `src/api/dependencies.py`, `src/api/v1/health.py`, `src/api/v1/webhook.py`.

---

### 🏗️ Etapa 1: Infraestructura Base, Configuración, Modelos SQLAlchemy 2.0 y Migración Alembic (Completada)
- [x] Crear `pyproject.toml`, `.env.example`, `docker-compose.yml`, `src/core/` (`config.py`, `exceptions.py`, `database.py`, `redis.py`).
- [x] Crear `tests/conftest.py`, `tests/test_stage1_models_and_infra.py`, `src/models/` (`base.py`, `user.py`, `schedule.py`, `appointment.py`) y migraciones Alembic (`0001_initial_schema.py`, `0002_seed_initial_admin.py`).
