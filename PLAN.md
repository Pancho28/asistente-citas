# PLAN.md — Plan Activo de Implementación (Etapa 9: Jornada Partida, Desambiguación de Homónimos/Citas y Notificaciones Cruzadas)

> **Estado del Proyecto Base:** Las Etapas 1 a 8 están completadas al 100% (**62/62 tests en verde**).  
> **Objetivo de este Documento:** Definir el plan técnico (Fase 1 ➔ Fase 2 TDD) para cerrar los **3 puntos restantes del Backlog Post-MVP**:
> 1. **Jornada partida en un mismo día (Horario de almuerzo fijo / 2.º turno)**
> 2. **Desambiguación de homónimos o múltiples citas activas (`resolver_cita_medico`)**
> 3. **Notificaciones proactivas cruzadas en tiempo real (Médico $\leftrightarrow$ Paciente)**

---

## 🕒 Mejora 1: Jornada Partida en un Mismo Día (`hora_apertura_2` y `hora_cierre_2`)

### Problema Actual
En [`src/services/onboarding_service.py`](./src/services/onboarding_service.py) y [`src/agent/mcp_tools/doctor.py`](./src/agent/mcp_tools/doctor.py), `configurar_horario_base_y_perfil` solo recibe un par `hora_apertura` / `hora_cierre`. Si el médico dice *"Atiendo de lunes a viernes de 08:00 a 12:00 y de 14:00 a 18:00 (de 12 a 2 almuerzo)"*, solo podía registrar un tramo continuo por día.
Además, en [`_formatear_horario_semanal`](./src/services/appointment_service.py), el diccionario `por_dia = {hb.dia_semana: hb ...}` sobrescribía el primer turno si un día tenía dos bloques en `HorarioBase`.

### Solución Propuesta
1. **En [`src/services/onboarding_service.py`](./src/services/onboarding_service.py) (`configurar_horario_base_y_perfil`):**
   - Añadir parámetros opcionales `hora_apertura_2: time | None = None` y `hora_cierre_2: time | None = None`.
   - Dentro del bloque `if dias_semana and hora_apertura and hora_cierre:`:
     - Crear el primer bloque `HorarioBase(dia_semana=dia, hora_inicio=hora_apertura, hora_fin=hora_cierre, activo=True)`.
     - Si además se enviaron `hora_apertura_2` y `hora_cierre_2`, crear un segundo bloque `HorarioBase(dia_semana=dia, hora_inicio=hora_apertura_2, hora_fin=hora_cierre_2, activo=True)` para cada `dia` en `dias_semana`.
2. **En [`src/agent/mcp_tools/doctor.py`](./src/agent/mcp_tools/doctor.py) (`configurar_horario_base_y_perfil`):**
   - Añadir `hora_apertura_2: str | None = None` y `hora_cierre_2: str | None = None` a la firma MCP, parsearlos con `time.fromisoformat` y pasarlos a `service_configurar_perfil`.
3. **En [`src/services/appointment_service.py`](./src/services/appointment_service.py) (`_formatear_horario_semanal`):**
   - Agrupar todos los bloques activos de un mismo `dia_semana` ordenados por `hora_inicio`, de modo que:
     - Un turno continuo se formatee como `"Lunes de 09:00 a 17:00"`.
     - Una jornada partida se formatee como `"Lunes de 08:00 a 12:00 y de 14:00 a 18:00"`.

---

## 👥 Mejora 2: Desambiguación de Homónimos o Múltiples Citas Futuras (`resolver_cita_medico`)

### Problema Actual
En [`src/services/appointment_service.py`](./src/services/appointment_service.py), cuando el médico pide mover, cancelar o registrar el pago de un paciente por nombre (ej. *"Carlos"*), si existen **dos pacientes llamados Carlos** con citas próximas (ej. *"Carlos Pérez"* y *"Carlos Gómez"*) o el mismo paciente tiene **dos citas futuras activas** (ej. lunes y jueves), `resolver_cita_medico` tomaba silenciosamente `proximas[0]` sin avisarle al médico que había más de una coincidencia.

### Solución Propuesta
1. **En [`src/services/appointment_service.py`](./src/services/appointment_service.py) (`resolver_cita_medico`):**
   - Añadir un filtro opcional `fecha_referencia: date | None = None` por si se conoce la fecha.
   - Al evaluar `candidatas = proximas if proximas else coincidencias`:
     - Si hay una coincidencia exacta única de nombre completo entre varias parciales, priorizarla si solo ella tiene una única cita próxima.
     - Si `len(candidatas) > 1`:
       - Construir un resumen claro de las citas en conflicto:
         ```python
         opciones = [
             f"- {c.paciente.nombre_completo} el {c.fecha_hora_inicio.strftime('%Y-%m-%d a las %H:%M')} (cita_id: {c.id})"
             for c in candidatas
         ]
         ```
       - Lanzar `ValidationError("Existen múltiples citas activas que coinciden con ese nombre:\n" + "\n".join(opciones) + "\nPor favor pregúntale al médico a cuál se refiere y vuelve a invocar la herramienta usando el cita_id exacto.")`.
   - De este modo, el wrapper MCP captura `ValidationError` y se lo entrega a Gemini con los `cita_id` exactos para que el médico elija con total seguridad.

---

## 🔔 Mejora 3: Notificaciones Proactivas Cruzadas en Tiempo Real (Médico $\leftrightarrow$ Paciente)

### Problema Actual
Cuando el paciente agenda, reagenda o cancela una cita, el médico no recibe un mensaje instantáneo en su chat. Y cuando el médico mueve o cancela manualmente la cita de un paciente desde su chat de médico, el paciente tampoco recibe un aviso inmediato en su chat de Telegram/WhatsApp.

### Solución Propuesta
1. **Crear helper asíncrono *best-effort* `_notificar_usuario_directo(usuario: Usuario | None, texto: str) -> None` en [`src/services/appointment_service.py`](./src/services/appointment_service.py):**
   - Si `not usuario` o `not usuario.external_chat_id` o `usuario.external_chat_id.startswith("manual_")` (paciente manual sin chat real), retorna silenciosamente sin hacer nada.
   - Selecciona `TelegramAdapter()` si `usuario.canal_origen == "TELEGRAM"` o `WhatsAppAdapter()` si es `"WHATSAPP"`.
   - Envía `await adapter.send_message(recipient=usuario.external_chat_id, text=texto)` dentro de un bloque `try / except Exception as exc: logger.warning(...)` para que un fallo de red externa **jamás** revierta ni rompa la transacción de base de datos.
2. **Puntos de disparo en [`src/services/appointment_service.py`](./src/services/appointment_service.py):**
   - **Paciente agenda (`agendar_cita_paciente`):** Notifica al `perfil.usuario` (médico):  
     *"📅 Nueva cita agendada: {nombre_paciente} para el {fecha} a las {hora}."*
   - **Paciente cancela o reagenda (`cancelar_o_reagendar_cita_paciente`):**
     - En `"CANCELAR"`, cargar `selectinload(Cita.profesional).selectinload(PerfilProfesional.usuario)` y notificar al médico:  
       *"❌ Cita cancelada por el paciente {nombre_paciente} ({fecha} a las {hora})."*
     - En `"REAGENDAR"`, notificar al médico del cambio de horario.
   - **Médico mueve o cancela manualmente (`agendar_o_mover_cita_manual_medico`):**
     - En `"MOVER"`, notificar al `cita.paciente`:  
       *"🔄 El Dr./Dra. {nombre_medico} ha reprogramado tu cita para el {fecha_destino} a las {hora_destino}."*
     - En `"CANCELAR"`, notificar al `cita.paciente`:  
       *"❌ El Dr./Dra. {nombre_medico} ha cancelado tu cita del {fecha_anterior} a las {hora_anterior}."*

---

## 🧪 Ciclo TDD (Pruebas a Añadir en `tests/`)

1. **`test_doctor_split_shift_schedule_generation`** (en [`tests/test_stage2b_finance_and_onboarding.py`](./tests/test_stage2b_finance_and_onboarding.py) y [`tests/test_stage2b_availability_and_appointments.py`](./tests/test_stage2b_availability_and_appointments.py)):
   - Configura jornada partida Lunes (`dia_semana=1`) de `08:00` a `10:00` y de `14:00` a `16:00` (`hora_apertura_2=time(14,0)`, `hora_cierre_2=time(16,0)`).
   - Verifica que `consultar_disponibilidad` ofrece `["08:00", "08:30", "09:00", "09:30", "14:00", "14:30", "15:00", "15:30"]` (excluyendo el almuerzo de 10:00 a 14:00) y que `_formatear_horario_semanal` devuelve `"Lunes de 08:00 a 10:00 y de 14:00 a 16:00"`.
2. **`test_doctor_disambiguation_on_homonym_patients`** (en [`tests/test_stage2b_availability_and_appointments.py`](./tests/test_stage2b_availability_and_appointments.py)):
   - Crea dos citas futuras para `"Carlos Pérez"` y `"Carlos Gómez"`.
   - Verifica que al llamar `agendar_o_mover_cita_manual_medico(..., accion="CANCELAR", nombre_paciente="Carlos")` lanza `ValidationError` listando ambas opciones y sus `cita_id`, y que al pasar el `cita_id` específico cancela la cita correcta.
3. **`test_realtime_cross_notifications_between_doctor_and_patient`** (en [`tests/test_stage2b_availability_and_appointments.py`](./tests/test_stage2b_availability_and_appointments.py)):
   - Con `monkeypatch` sobre `_notificar_usuario_directo` (o `TelegramAdapter.send_message`), verifica que cuando el paciente agenda se emite notificación al `external_chat_id` del médico, y cuando el médico mueve o cancela la cita manualmente se emite notificación al `external_chat_id` del paciente (y se ignora si es un paciente `"manual_..."`).
