# PLAN.md — Plan Activo de Implementación (Etapa 7: Las 8 Mejoras Operativas del Día a Día para Médico y Paciente)

> **Estado del Proyecto Base:** Las Etapas 1 a 6 están completadas al 100% (**54/54 tests en verde**).  
> **Objetivo de este Documento:** Definir de forma limpia y quirúrgica el plan de implementación (Fase 1 ➔ Fase 2 TDD) para las **8 mejoras de casos reales del día a día** en los flujos de **Médico** y **Paciente**.

---

## 🩺 Bloque A: Mejoras Operativas del Rol MÉDICO (Puntos 1 al 5)

### Mejora 1: Soportar `accion="CANCELAR"` en `agendar_o_mover_cita_manual`
* **Problema actual:** El médico solo tenía `"CREAR"` y `"MOVER"` en `agendar_o_mover_cita_manual`. Si un paciente le avisaba por fuera que no iría, el médico no podía pedir por texto *"Cancela la cita de Roberto de mañana"*.
* **Solución:**
  1. En [`src/services/appointment_service.py`](./src/services/appointment_service.py) (`agendar_o_mover_cita_manual_medico`):
     - Cambiar `accion: Literal["CREAR", "MOVER", "CANCELAR"]` y hacer opcionales `fecha_destino: date | None = None` y `hora_destino: time | None = None` (solo requeridos en `"CREAR"` y `"MOVER"`).
     - Si `accion == "CANCELAR"`, resolver la cita del médico, cambiar `cita.estado = "CANCELADA"` y `cita.estado_financiero = "EXONERADO"`, hacer `commit()` y devolver la cita.
  2. En [`src/agent/mcp_tools/doctor.py`](./src/agent/mcp_tools/doctor.py) (`agendar_o_mover_cita_manual`):
     - Permitir `act in ("CREAR", "MOVER", "CANCELAR")` y hacer opcionales `fecha_destino: str | None = None` y `hora_destino: str | None = None`.

### Mejora 2: Resolución de Citas del Médico por Nombre del Paciente o Fecha/Hora (Sin exigir UUID)
* **Problema actual:** Cuando el médico dice *"Regístrale pago de $50 a Laura"* o *"Mueve la cita de Roberto a las 16:00"*, `registrar_pago_o_descuento` y `agendar_o_mover_cita_manual` hacían `UUID(cita_id)` y fallaban con `INVALID_UUID` si Gemini pasaba `"Laura"` o `"Roberto"`.
* **Solución:**
  1. Crear helper `async def resolver_cita_medico(db: AsyncSession, perfil_id: UUID, cita_id_or_query: UUID | str | None = None, nombre_paciente: str | None = None) -> Cita` en [`src/services/appointment_service.py`](./src/services/appointment_service.py):
     - Si `cita_id_or_query` es un `UUID` válido (o string UUID), busca por `Cita.id == uuid_val`.
     - Si no es un UUID (ej. `"Laura"`, `"Roberto"`) o se pasó `nombre_paciente`, busca entre las citas no canceladas del médico aquella cuyo `paciente.nombre_completo` coincida usando `_normalize_text` (insensible a tildes/mayúsculas), priorizando las más recientes/próximas.
  2. Usar `resolver_cita_medico` tanto en `agendar_o_mover_cita_manual_medico` (`MOVER` / `CANCELAR`) como en `registrar_pago_o_descuento` ([`src/services/finance_service.py`](./src/services/finance_service.py)).
  3. En [`src/agent/mcp_tools/doctor.py`](./src/agent/mcp_tools/doctor.py), no rechazar `cita_id` cuando no sea un UUID de 36 caracteres; pasarlo como `str` al servicio para que `resolver_cita_medico` lo resuelva por nombre del paciente.

### Mejora 3: Exponer `forzar_con_citas: bool = False` en `bloquear_horario_agenda`
* **Problema actual:** `service_bloquear_horario` en `appointment_service.py` ya soportaba `forzar_con_citas: bool = False`, pero el wrapper MCP `bloquear_horario_agenda` en [`src/agent/mcp_tools/doctor.py`](./src/agent/mcp_tools/doctor.py) no lo incluía en su firma, impidiendo al médico confirmar un bloqueo de emergencia cuando tenía citas agendadas.
* **Solución:**
  - Añadir `forzar_con_citas: bool = False` a la firma de `bloquear_horario_agenda` en [`src/agent/mcp_tools/doctor.py`](./src/agent/mcp_tools/doctor.py) y pasarlo a `service_bloquear_horario(...)`.

### Mejora 4: Prevención de Colisiones (`SlotTakenError`) al Crear o Mover Citas Manualmente por el Médico
* **Problema actual:** `agendar_o_mover_cita_manual_medico` insertaba o movía la cita sin verificar si ya existía otra cita activa del médico en ese mismo horario, causando un `IntegrityError` no controlado en PostgreSQL.
* **Solución:**
  - En `agendar_o_mover_cita_manual_medico` ([`src/services/appointment_service.py`](./src/services/appointment_service.py)), antes de hacer `commit()` en `"CREAR"` o `"MOVER"`, verificar si existe otra `Cita` activa (`estado != "CANCELADA"` y `Cita.id != cita.id`) que se solape con `[slot_start, slot_end)`. Si existe (o si salta `IntegrityError`), lanzar `SlotTakenError("El horario seleccionado ya está ocupado por otro paciente en su agenda.")`.
  - Capturar `SlotTakenError` en `agendar_o_mover_cita_manual` ([`src/agent/mcp_tools/doctor.py`](./src/agent/mcp_tools/doctor.py)) devolviendo `{"success": False, "code": "SLOT_TAKEN", "message": exc.message}`.

### Mejora 5: Actualización Incremental por Día en `configurar_horario_base_y_perfil`
* **Problema actual:** En [`src/services/onboarding_service.py`](./src/services/onboarding_service.py), `configurar_horario_base_y_perfil` desactivaba **todos** los `horarios_base` de la semana (`for hb in perfil.horarios_base: hb.activo = False`). Si el médico tenía Lunes a Viernes de 9 a 5 y pedía *"Agrega los sábados de 9 a 1"*, se le borraban Lunes a Viernes.
* **Solución:**
  - Desactivar únicamente los registros previos cuyo `hb.dia_semana in dias_semana`:
    ```python
    for hb in perfil.horarios_base:
        if hb.dia_semana in dias_semana:
            hb.activo = False
    ```
  - Así, si el médico envía `dias_semana=[6]`, solo actualiza el sábado y conserva intactos los horarios de lunes a viernes.

---

## 🧑‍💼 Bloque B: Mejoras Operativas del Rol PACIENTE (Puntos 6 al 8)

### Mejora 6: Preservar Nombre del Paciente y Liberar Lock de Redis al Reagendar (`REAGENDAR`)
* **Problema actual:** En `cancelar_o_reagendar_cita_paciente` ([`src/services/appointment_service.py`](./src/services/appointment_service.py)), la rama `accion == "REAGENDAR"` no liberaba el lock de Redis del horario anterior (`release_slot_lock`) antes de agendar el nuevo horario, y pasaba `nombre_completo_paciente=""`.
* **Solución:**
  - En `accion == "REAGENDAR"`, cargar `selectinload(Cita.paciente)`, liberar primero el lock de Redis del slot viejo (`await release_slot_lock(...)`) y pasar `nombre_completo_paciente=cita.paciente.nombre_completo or "Paciente"` al crear la nueva cita.

### Mejora 7: Cancelación o Reagendamiento del Paciente sin Exigir `cita_id` UUID
* **Problema actual:** En `cancelar_o_reagendar_cita_propia` ([`src/agent/mcp_tools/patient.py`](./src/agent/mcp_tools/patient.py)), `cita_id: str` exigía un UUID estricto. Si el paciente decía *"Quiero cancelar mi cita de mañana"*, fallaba si no se le pasaba el UUID exacto.
* **Solución:**
  - Hacer `cita_id: str | None = None` en `cancelar_o_reagendar_cita_propia` ([`src/agent/mcp_tools/patient.py`](./src/agent/mcp_tools/patient.py)) y permitir `cita_id: UUID | str | None = None` en `cancelar_o_reagendar_cita_paciente` ([`src/services/appointment_service.py`](./src/services/appointment_service.py)).
  - Si `cita_id` es `None` o no es un UUID (ej. una fecha `"2026-11-10"`), buscar automáticamente las citas futuras activas del paciente (`CONFIRMADA` / `CONFIRMADA_PACIENTE`); si el string parece una fecha `YYYY-MM-DD`, filtrar por esa fecha, o tomar la próxima cita activa del paciente.

### Mejora 8: Validación de Horario Laboral (`HorarioBase`) al Agendar Cita como Paciente
* **Problema actual:** Si un paciente pedía directamente *"Agéndame el domingo a las 11:00 PM"* sin pasar antes por `consultar_disponibilidad`, `agendar_cita_paciente` solo revisaba que no hubiera otra cita o bloqueo a esa hora, pero **no verificaba que el médico trabajara ese día/hora en `HorarioBase`**.
* **Solución:**
  - En `agendar_cita_paciente` ([`src/services/appointment_service.py`](./src/services/appointment_service.py)), si el profesional tiene registros activos en `HorarioBase` (es decir, ya configuró su jornada semanal), verificar que para el `dia_semana_spec = (fecha.weekday() + 1) % 7` exista al menos un `HorarioBase` activo donde `hb.hora_inicio <= hora` y `slot_end.time() <= hb.hora_fin`.
  - Si el horario está fuera de la jornada laboral del médico, lanzar `ValidationError("El médico no atiende en el día u horario solicitado. Por favor consulta los horarios disponibles.")`.
  - *(Nota de compatibilidad con tests existentes: si un médico en un test unitario aislado no tiene ningún `HorarioBase` creado en absoluto en toda la semana, se omite el chequeo de `HorarioBase` para no romper fixtures minimalistas; si tiene al menos un `HorarioBase` configurado en su perfil, se exige que la cita caiga dentro de su jornada activa).*

---

## 🧪 Ciclo TDD (Pruebas a Añadir en `tests/`)

En [`tests/test_stage2b_availability_and_appointments.py`](./tests/test_stage2b_availability_and_appointments.py) y [`tests/test_stage3_mcp_security_and_tools.py`](./tests/test_stage3_mcp_security_and_tools.py):
1. `test_doctor_manual_cancel_and_collision_prevention`: Verifica que el médico puede cancelar una cita manualmente (`accion="CANCELAR"`), resolver por `nombre_paciente` sin UUID, y que recibe `SlotTakenError` si intenta agendar dos citas manuales a la misma hora.
2. `test_doctor_block_schedule_force_with_appointments`: Verifica que la tool MCP `bloquear_horario_agenda` expone `forzar_con_citas=True` y permite forzar el bloqueo cuando hay citas.
3. `test_doctor_payment_by_patient_name_and_incremental_schedule`: Verifica que `registrar_pago_o_descuento` acepta el nombre del paciente (ej. `"Laura"`) en `cita_id`, y que `configurar_horario_base_y_perfil` al agregar el sábado (`dias_semana=[6]`) conserva intactos los horarios de lunes a viernes.
4. `test_patient_cancel_without_uuid_and_out_of_hours_validation`: Verifica que el paciente puede cancelar/reagendar su próxima cita sin pasar UUID, y que `agendar_cita_paciente` rechaza con `ValidationError` intentos de agendar fuera del horario laboral del médico (ej. domingo o a las 23:00).
