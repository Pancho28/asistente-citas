# Plan de Ejecución Activo (`PLAN.md`)
## Etapa 11: Suite Completa de Administración y Operación SaaS en el Chatbot (`ADMIN`)

> **Documento temporal de handoff entre modelos (`[PLAN]` ➔ `[EXEC]`):**  
> Este archivo se **sobrescribe por completo** al iniciar cada nueva tarea. Contiene el diseño exacto para que el modelo ejecutor implemente en TDD sin re-arquitecturar ni gastar tokens explorando todo el proyecto.

**Fecha:** `2026-11-25`  
**Objetivo:** Dotar al rol `ADMIN` de gestión integral desde el chatbot sobre doctores (por nombre/UUID, ficha 360°, edición de perfil/horario y baja), códigos de invitación (listado y revocación), pacientes (búsqueda, historial, edición y bloqueo efectivo en el webhook), soporte/intervención de citas, métricas operativas/financieras del SaaS y envío de comunicados a médicos.  
**Referencia en SPEC:** `SPEC.md` (§3.2) y `skills/administrador_skills.md` (§1 a §8).

---

## 1. Diseño Técnico y Contratos Exactos

- **Impacto en Gateway (`api-gateway-auth`):** Ninguno.
- **Impacto en Base de Datos (`citas_medicas`):** Ninguna migración nueva requerida (reutiliza las columnas actuales de `usuarios`, `perfiles_profesionales`, `codigos_invitacion`, `horarios_base`, `bloqueos_agenda`, `citas` y `pagos`, incluyendo `zona_horaria` y `onboarding_campos_configurados` de la migración `0003`).

---

### 1.1. Permisos RBAC y Bloqueo Efectivo de Cuentas Inactivas (`src/core/security.py`, `src/schemas/context.py`, `src/api/dependencies.py`, `src/api/v1/webhook.py`)

1. **Mapa de permisos `RoleEnum.ADMIN` en `src/core/security.py`:**
   ```python
   RoleEnum.ADMIN: frozenset({
       "admin:generar_invitacion",
       "admin:gestionar_profesionales",
       "admin:gestionar_pacientes",
       "admin:soporte_citas",
       "admin:ver_metricas",
       "admin:enviar_comunicados",
       "admin:cambiar_vista",
       "disponibilidad:consultar",
       "agenda:ver_pacientes_dia",
       "finanzas:ver_reportes",
   }),
   ```
2. **Campo `is_active: bool = True` en `UserContext` (`src/schemas/context.py` y `src/api/dependencies.py`):**
   - En `UserContext`: añadir `is_active: bool = True`.
   - En `get_user_context` (`dependencies.py` Modo A): poblar `is_active=bool(user.activo)` (si `user.rol_sistema == "ADMIN"`, mantener `is_active=True` como salvaguarda anti-lockout). En Modo B: consultar opcionalmente si `user_uuid` existe en BD con `activo == False`; si no existe en BD (tests aislados de cabeceras), mantener `is_active=True`.
3. **Cortafuegos en `receive_webhook` (`src/api/v1/webhook.py`):**
   - Antes del semáforo de Ruta Rápida / Ruta IA:
     ```python
     if not ctx.is_active:
         suspended_msg = (
             "Tu cuenta se encuentra temporalmente suspendida. "
             "Por favor contacta al administrador para más información."
         )
         await adapter.send_message(recipient=ctx.external_chat_id, text=suspended_msg)
         return {
             "status": "blocked_inactive_user",
             "event_type": event.event_type,
             "user_id": str(ctx.user_id),
             "role": ctx.role.value,
             "channel": ctx.channel.value,
             "reply_text": suspended_msg,
         }
     ```
   - Garantiza **0 consumo de tokens de Gemini** y bloqueo de botones rápidos para pacientes o médicos dados de baja.

---

### 1.2. Nuevo Servicio de Dominio Administrativo (`src/services/admin_service.py`)

Centraliza toda la lógica SQL y reglas de negocio administrativas cumpliendo separación estricta de capas (`AGENTS.md` §5.1):

1. **Resolución flexible de médico y paciente (sin exigir UUID):**
   - `resolver_profesional_admin(db: AsyncSession, identificador: str | UUID) -> PerfilProfesional`:
     - Busca por UUID exacto (`PerfilProfesional.id` o `PerfilProfesional.usuario_id`) o coincidencia normalizada (`_normalize_text`) en `Usuario.nombre_completo` o `PerfilProfesional.especialidad`.
     - Si hay >1 coincidencia parcial y ninguna exacta, lanza `ValidationError` listando los candidatos con su nombre, especialidad y `profesional_id`.
     - Si no hay coincidencias, lanza `EntityNotFoundError("Profesional médico")`.
   - `resolver_paciente_admin(db: AsyncSession, identificador: str | UUID) -> Usuario`:
     - Busca en `Usuario` (`rol_sistema == "PACIENTE"`) por UUID exacto, `external_chat_id`, `telefono` o coincidencia normalizada en `nombre_completo`.
     - Si hay múltiples coincidencias ambiguas, lanza `ValidationError` listando nombre, teléfono/chat_id y UUID.

2. **Gestión de Códigos de Invitación (`gestionar_codigos_invitacion_admin`):**
   - `accion == "LISTAR"`: filtra `CodigoInvitacion` según `filtro_estado` (`"VIGENTES"`: `usado == False and expira_en > now_tz()`, `"USADOS"`: `usado == True`, `"TODOS"`), incluyendo nombre del médico que lo canjeó si aplica.
   - `accion == "REVOCAR"`: busca por `frase_codigo.strip().upper()`; si ya fue usado lanza `ValidationError("El código ya fue canjeado y no puede revocarse.")`; si está vigente, marca `expira_en = now_tz() - timedelta(seconds=1)` (expiración inmediata trazable) y retorna `{"success": True, "code": "INVITATION_REVOKED", ...}`.

3. **Gestión Avanzada de Doctores (`gestionar_profesionales_admin`):**
   - Mantiene `accion == "LISTAR"` enriquecido (incluye `usuario_activo`, `zona_horaria`, `onboarding_completo` y `citas_futuras_activas`).
   - `accion == "DETALLE"`: usa `resolver_profesional_admin`, retorna ficha completa 360° (datos de usuario, horario semanal con `_formatear_horario_semanal`, tarifa, duración, resumen diario, `obtener_estado_onboarding_medico` y conteo de citas futuras/completadas).
   - `accion in ("ACTIVAR", "SUSPENDER")`: usa `resolver_profesional_admin` (acepta nombre o UUID) y cambia `perfil.acepta_nuevas_citas`.
   - `accion == "EDITAR"`: usa `resolver_profesional_admin`; actualiza `nuevo_nombre`, `nuevo_telefono`, `nueva_especialidad` y delega cambios de `zona_horaria`, `tarifa_base_consulta`, `duracion_cita_minutos`, `dias_semana`, `hora_apertura`, `hora_cierre` a `configurar_horario_base_y_perfil`.
   - `accion in ("DAR_DE_BAJA", "REACTIVAR_CUENTA")`:
     - En `"DAR_DE_BAJA"`: cuenta citas futuras confirmadas (`Cita.fecha_hora_inicio >= now_wall_utc()`). Si existen y `forzar == False`, retorna `{"success": False, "code": "HAS_ACTIVE_APPOINTMENTS", "citas_futuras": count, ...}`. Si no existen o `forzar == True`, marca `perfil.acepta_nuevas_citas = False` y `perfil.usuario.activo = False`.
     - En `"REACTIVAR_CUENTA"`: marca `perfil.usuario.activo = True` y `perfil.acepta_nuevas_citas = True`.

4. **Gestión de Pacientes (`gestionar_pacientes_admin`):**
   - `accion == "BUSCAR"`: filtra usuarios `rol_sistema == "PACIENTE"` por texto opcional (`nombre_completo`, `telefono`, `external_chat_id`), retornando hasta 25 resultados con conteos agregados (`total_citas`, `completadas`, `futuras`, `inasistencias`) y `activo`.
   - `accion == "DETALLE"`: usa `resolver_paciente_admin`, devuelve perfil del paciente + últimas 10 citas (con médico, fecha, hora y estado).
   - `accion == "EDITAR"`: actualiza `nombre_completo`, `telefono` y/o `zona_horaria` validada con `normalize_timezone_name`.
   - `accion in ("BLOQUEAR", "REACTIVAR")`: usa `resolver_paciente_admin` (rechaza con `PermissionDeniedError` si `rol_sistema == "ADMIN"`), actualiza `paciente.activo = (accion == "REACTIVAR")` y retorna confirmación.

5. **Mesa de Ayuda / Intervención de Citas (`gestionar_citas_soporte_admin`):**
   - `accion == "CONSULTAR"`: lista citas filtrando opcionalmente por `identificador_medico`, `identificador_paciente` y rango `[fecha_inicio, fecha_fin]`.
   - `accion == "CANCELAR_CITA"`: resuelve la cita por `cita_id` UUID (o próxima cita activa entre médico/paciente), marca `estado = "CANCELADA"`, `estado_financiero = "EXONERADO"`, libera lock en Redis y notifica con `_notificar_usuario_directo` a paciente y médico incluyendo el `motivo`.
   - `accion == "REPROGRAMAR_CITA"`: verifica colisión con `_check_manual_slot_collision`, mueve `fecha_hora_inicio`/`fecha_hora_fin`, libera lock anterior y notifica a paciente y médico.
   - `accion == "CANCELAR_JORNADA_MEDICO"`: resuelve al médico y la fecha `fecha_inicio`; cancela todas sus citas `CONFIRMADA`/`CONFIRMADA_PACIENTE` de ese día, crea un `BloqueoAgenda` de `00:00` a `23:59` con el `motivo` y notifica a cada paciente afectado, devolviendo `{"citas_canceladas": N, "pacientes_notificados": [...]}`.

6. **Métricas Operativas de la Plataforma (`consultar_metricas_plataforma_admin`):**
   - Resuelve rango según `periodo` (`"HOY"`, `"SEMANA"`, `"MES"`, `"PERSONALIZADO"`).
   - Calcula y retorna:
     - `medicos`: `total`, `activos_aceptando_citas`, `suspendidos_o_baja`, `con_onboarding_pendiente` (nombres y qué les falta).
     - `pacientes`: `total`, `activos`, `bloqueados`, `nuevos_en_periodo`.
     - `citas_en_periodo`: `total`, `por_estado` (`CONFIRMADA`, `CONFIRMADA_PACIENTE`, `COMPLETADA`, `CANCELADA`, `NO_ASISTIO`), `tasa_inasistencia_pct`.
     - `finanzas_en_periodo`: `total_cobrado` (de `Pago` `COMPLETADO` en el rango), `total_proyectado` (citas activas en el rango) y `ranking_medicos`.

7. **Comunicados Directos a Médicos (`enviar_comunicado_medicos_admin`):**
   - Selecciona destinatarios según `destinatario`:
     - `"TODOS_ACTIVOS"`: médicos con `Usuario.activo == True`.
     - `"ONBOARDING_PENDIENTE"`: médicos activos cuyo `obtener_estado_onboarding_medico` tenga `onboarding_completo == False`.
     - Cualquier otro string: resuelve a un médico puntual con `resolver_profesional_admin`.
   - Envía `f"📢 <b>Aviso de Administración:</b>\n{mensaje}"` por `TelegramAdapter` / `WhatsAppAdapter` (ignorando chats `manual_*`), contabilizando `enviados` y `fallidos`.

---

### 1.3. Capa MCP, Registro y System Prompt (`src/agent/mcp_tools/admin.py`, `src/agent/mcp_tools/registry.py`, `src/agent/prompts.py`)

1. **`src/agent/mcp_tools/admin.py`:** Exponer las 8 herramientas con docstrings detallados para Gemini Flash y decoradores `@require_permission(...)`:
   - `generar_codigo_invitacion_medico` (`admin:generar_invitacion`)
   - `gestionar_codigos_invitacion` (`admin:generar_invitacion`)
   - `listar_y_gestionar_profesionales` (`admin:gestionar_profesionales`)
   - `gestionar_pacientes` (`admin:gestionar_pacientes`)
   - `gestionar_citas_soporte` (`admin:soporte_citas`)
   - `consultar_metricas_plataforma` (`admin:ver_metricas`)
   - `enviar_comunicado_medicos` (`admin:enviar_comunicados`)
   - `cambiar_modo_vista_sesion` (`admin:cambiar_vista`)
2. **`src/agent/mcp_tools/registry.py`:** Registrar las 8 herramientas bajo `elif ctx.role == RoleEnum.ADMIN:`.
3. **`src/agent/prompts.py` (`RoleEnum.ADMIN`):** Actualizar las directrices del administrador explicando todas sus capacidades de gobierno (búsqueda por nombre sin pedir UUIDs innecesarios, advertencia previa antes de dar de baja médicos con citas activas o cancelar jornadas completas, resumen ejecutivo de métricas y envío de avisos).

---

## 2. Archivos a Crear o Modificar

1. **[MODIFICAR]** `src/core/security.py` — Ampliar `ROLE_PERMISSIONS_MAP[RoleEnum.ADMIN]`.
2. **[MODIFICAR]** `src/schemas/context.py` y `src/api/dependencies.py` — Añadir `is_active: bool = True` en `UserContext` y poblarlo desde `Usuario.activo`.
3. **[MODIFICAR]** `src/api/v1/webhook.py` — Bloquear usuarios con `ctx.is_active == False` devolviendo aviso de suspensión en 0 tokens.
4. **[CREAR]** `src/services/admin_service.py` — Implementar las funciones de negocio administrativo (doctores, códigos, pacientes, soporte de citas, métricas y comunicados).
5. **[MODIFICAR]** `src/agent/mcp_tools/admin.py` y `src/agent/mcp_tools/registry.py` — Exponer y registrar las nuevas herramientas MCP de `ADMIN`.
6. **[MODIFICAR]** `src/agent/prompts.py` — Actualizar directrices del System Prompt para `RoleEnum.ADMIN`.
7. **[CREAR]** `tests/test_stage11_admin_management_and_operations.py` — Suite TDD integral de todas las herramientas administrativas y del bloqueo efectivo en webhook.
8. **[MODIFICAR]** `TODO.md` — Marcar `[x]` cada ítem tras validar la suite en verde.

---

## 3. Plan TDD (Red ➔ Green ➔ Refactor)

1. **Fase Roja (Red):**
   - Crear `tests/test_stage11_admin_management_and_operations.py` cubriendo:
     1. **Doctores por nombre + Ficha 360° + Edición + Baja con protección de citas:** Buscar `"Dr. Pérez"` sin UUID, ver su ficha (`DETALLE`), editarle especialidad/horario/zona horaria (`EDITAR`), intentar `DAR_DE_BAJA` con cita futura (`forzar=False` advierte `HAS_ACTIVE_APPOINTMENTS`, `forzar=True` desactiva).
     2. **Códigos de invitación (`LISTAR` y `REVOCAR`):** Generar código, listarlo como vigente, revocarlo y comprobar que ya no puede canjearse (`InvalidInvitationCodeError`).
     3. **Pacientes + Bloqueo real en Webhook:** Buscar paciente con historial e inasistencias, editar su teléfono, ejecutar `BLOQUEAR` (`activo=False`) y verificar que una petición entrante al endpoint `/api/v1/webhook` de ese paciente devuelve `"blocked_inactive_user"` sin invocar Gemini ni permitir agendar; luego `REACTIVAR` restaura el acceso.
     4. **Soporte de citas y emergencia médica (`CANCELAR_JORNADA_MEDICO` y `REPROGRAMAR_CITA`):** Reprogramar una cita puntual desde admin y cancelar la jornada completa de un médico verificando que las citas pasan a `CANCELADA`, se crea el `BloqueoAgenda` y se envían notificaciones a los pacientes afectados.
     5. **Métricas SaaS y Comunicados (`consultar_metricas_plataforma` y `enviar_comunicado_medicos`):** Verificar conteos exactos de médicos (incluyendo `ONBOARDING_PENDIENTE`), pacientes, citas por estado, ingresos cobrados y envío de aviso filtrado solo a médicos con onboarding pendiente.
     6. **Seguridad RBAC (Doble Barrera):** Confirmar que `PACIENTE` y `MEDICO` no ven ninguna de las nuevas tools en `get_tools_for_user` y reciben `PermissionDeniedError` si intentan invocarlas.
   - Ejecutar: `pytest tests/test_stage11_admin_management_and_operations.py -q` (confirmar fallo en Rojo).
2. **Fase Verde (Green):**
   - Implementar los módulos y herramientas según la Sección 1.
3. **Fase Refactor y Validación Global:**
   - Verificar limpieza de código, límites de responsabilidad y correr `pytest -q`.
