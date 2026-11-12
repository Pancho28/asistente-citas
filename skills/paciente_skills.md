# paciente_skills.md — Herramientas Exclusivas del Rol PACIENTE

**Módulo de implementación:** `src/agent/mcp_tools/patient.py`  
**Rol requerido:** `PACIENTE` (o `ADMIN` usando `/modo_paciente`)

---

## 1. `consultar_disponibilidad`
Calcula los bloques horarios libres cruzando en memoria `HORARIOS_BASE`, restando `BLOQUEOS_AGENDA`, `CITAS` activas y locks temporales en Redis.

* **Permiso requerido:** `disponibilidad:consultar`
* **Parámetros expuestos al LLM:**
  * `fecha` (`str`, requerido): Fecha solicitada en formato `YYYY-MM-DD`.
  * `especialidad` (`str`, opcional): Especialidad médica buscada. Si solo hay un profesional en el sistema o no se especifica, filtra por defecto.
  * `turno_preferido` (`str`, opcional): `"MANANA"` (06:00-12:00), `"TARDE"` (12:00-19:00) o `"TODOS"`.
* **Parámetros inyectados por Gateway:** Ninguno.
* **Retorno esperado:**
  ```json
  {
    "success": true,
    "fecha": "2026-11-09",
    "profesional_id": "uuid-prof",
    "nombre_profesional": "Dr. Pérez",
    "especialidad": "Medicina General",
    "tarifa_consulta": 45.00,
    "moneda": "USD",
    "horarios_libres": ["09:00", "09:30", "11:00"],
    "ui_hint": "SHOW_BOOKING_BUTTONS"
  }
  ```

---

## 2. `agendar_cita_paciente`
Aparta un bloqueo atómico en Redis (`SET NX EX 300`), valida que el horario siga libre e inserta la cita en PostgreSQL copiando la `tarifa_base_consulta` a `monto_acordado`.

* **Permiso requerido:** `citas:agendar_propia`
* **Parámetros expuestos al LLM:**
  * `fecha` (`str`, requerido): Formato `YYYY-MM-DD`.
  * `hora` (`str`, requerido): Formato 24h `HH:MM`.
  * `nombre_completo_paciente` (`str`, requerido): Nombre y apellido del paciente (si ya existe en `USUARIOS.nombre_completo`, se reutiliza).
  * `motivo_consulta` (`str | None`, opcional, default `None`): **NUNCA se debe preguntar ni pedir al paciente.** Solo se envía si el paciente lo mencionó espontáneamente en la conversación.
  * `profesional_id` (`str`, opcional): UUID del profesional (si fue devuelto previamente por `consultar_disponibilidad`).
* **Parámetros inyectados por Gateway:** `paciente_id = X-User-Id`
* **Reglas de negocio e Instrucción en el Docstring del LLM:**
  * El `docstring` de la función debe instruir explícitamente a Gemini: *"NO preguntes el motivo de consulta al paciente; déjalo vacío a menos que el usuario lo haya escrito por iniciativa propia"*.
  * Si el horario fue tomado hace milisegundos por otro usuario, captura el `IntegrityError` del índice único parcial `idx_cita_unica_activa` y devuelve `"success": false, "code": "SLOT_TAKEN"`.
  * Notifica asíncronamente al profesional sobre la nueva cita agendada.

---

## 3. `ver_mis_citas_activas`
Lista todas las citas futuras del paciente que se encuentren en estado `CONFIRMADA` o `CONFIRMADA_PACIENTE`.

* **Permiso requerido:** `citas:ver_propias`
* **Parámetros expuestos al LLM:** Ninguno (llamada sin argumentos).
* **Parámetros inyectados por Gateway:** `paciente_id = X-User-Id`
* **Retorno esperado:** Lista de citas con `cita_id`, `fecha`, `hora`, `nombre_profesional`, `direccion_consultorio`, `monto_acordado` y `estado_financiero`.

---

## 4. `cancelar_o_reagendar_cita_propia`
Permite al paciente cancelar una cita futura o moverla a un nuevo horario disponible, siempre que se cumpla el tiempo mínimo de anticipación configurado por el médico.

* **Permiso requerido:** `citas:modificar_propia`
* **Parámetros expuestos al LLM:**
  * `cita_id` (`str`, opcional): UUID de la cita, fecha `YYYY-MM-DD` o vacío (`None`). Si no es un UUID o se omite, el backend resuelve automáticamente la próxima cita activa del paciente.
  * `accion` (`str`, requerido): `"CANCELAR"` o `"REAGENDAR"`.
  * `nueva_fecha` (`str`, opcional): Requerido solo si `accion == "REAGENDAR"` (`YYYY-MM-DD`).
  * `nueva_hora` (`str`, opcional): Requerido solo si `accion == "REAGENDAR"` (`HH:MM`).
* **Parámetros inyectados por Gateway:** `paciente_id = X-User-Id`
* **Reglas de negocio:**
  * Verifica que `cita.paciente_id == X-User-Id`.
  * Calcula `horas_restantes = (cita.fecha_hora_inicio - NOW())`. Si `horas_restantes < perfil_profesional.min_horas_anticipacion_cancelar`, rechaza la operación con `"code": "CANCELLATION_WINDOW_EXPIRED"` e indica al paciente que debe contactar directamente al consultorio.
  * Si es válido, libera el bloque original en Redis y BD (`release_slot_lock`) y avisa al médico. En `"REAGENDAR"`, conserva intacto el `nombre_completo` del paciente.

---

## 5. `canjear_codigo_invitacion_medico`
Permite a un usuario registrado como `PACIENTE` introducir una frase o código secreto generado por el `ADMIN` para convertirse en `MEDICO`.

* **Permiso requerido:** `onboarding:canjear_codigo` (Habilitado por defecto para `PACIENTE`).
* **Parámetros expuestos al LLM:**
  * `frase_codigo` (`str`, requerido): Código exacto proporcionado por el usuario (ej. `"MED-PSICO-8492"`).
  * `nombre_profesional` (`str`, requerido): Nombre y apellido con título (ej. `"Dr. Carlos Mendoza"`).
* **Parámetros inyectados por Gateway:** `usuario_id = X-User-Id`
* **Reglas de negocio:**
  * Busca en `CODIGOS_INVITACION` donde `frase_codigo == input`, `usado == false` y `expira_en > NOW()`.
  * Si es válido, cambia `USUARIOS.rol_sistema = 'MEDICO'`, `modo_vista_actual = 'MEDICO'`, crea el registro inicial en `PERFILES_PROFESIONALES` y marca el código como `usado = true`.