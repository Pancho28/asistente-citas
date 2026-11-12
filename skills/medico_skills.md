# medico_skills.md — Herramientas Exclusivas del Rol MEDICO

**Módulo de implementación:** `src/agent/mcp_tools/doctor.py`  
**Rol requerido:** `MEDICO` (o `ADMIN` usando `/modo_medico`)

---

## 1. `ver_agenda_profesional`
Obtiene la lista cronológica de citas del médico para un día o rango de fechas, incluyendo motivo de consulta (si existe), estado de confirmación del recordatorio y estado de pago.

* **Permiso requerido:** `agenda:ver_pacientes_dia`
* **Parámetros expuestos al LLM:**
  * `fecha_inicio` (`str`, requerido): Fecha inicial `YYYY-MM-DD`.
  * `fecha_fin` (`str`, opcional): Fecha final `YYYY-MM-DD` (si se omite, consulta solo `fecha_inicio`).
  * `filtro_estado` (`str`, opcional): `"TODAS"`, `"PENDIENTES_HOY"` (solo las que faltan por atender en el día), `"COMPLETADAS"`, `"IMPAGAS"`.
* **Parámetros inyectados por Gateway:** `profesional_usuario_id = X-User-Id`
* **Retorno esperado:** Lista detallada con `cita_id`, `hora_inicio`, `nombre_paciente`, `telefono`, `motivo_consulta`, `estado`, `respuesta_recordatorio`, `monto_acordado`, `monto_pagado` y `estado_financiero`, más `"ui_hint": "SHOW_DOCTOR_ACTION_BUTTONS"`.

---

## 2. `bloquear_horario_agenda`
Registra una excepción en `BLOQUEOS_AGENDA` por vacaciones, congreso o tarde libre para que esos horarios dejen de ofrecerse inmediatamente a los pacientes.

* **Permiso requerido:** `agenda:bloquear_horario`
* **Parámetros expuestos al LLM:**
  * `fecha_inicio` (`str`, requerido): `YYYY-MM-DD`.
  * `hora_inicio` (`str`, requerido): `HH:MM` (usar `"00:00"` para día completo).
  * `fecha_fin` (`str`, requerido): `YYYY-MM-DD`.
  * `hora_fin` (`str`, requerido): `HH:MM` (usar `"23:59"` para día completo).
  * `motivo` (`str`, opcional): Ej. `"Tarde libre"`, `"Congreso médico"`.
  * `forzar_con_citas` (`bool`, opcional, default `False`): Si es `True`, registra el bloqueo incluso si existen citas confirmadas en ese rango (tras haber advertido al médico).
* **Parámetros inyectados por Gateway:** `profesional_usuario_id = X-User-Id`
* **Reglas de negocio:**
  * Antes de bloquear, verifica si ya existen `CITAS` confirmadas en ese rango. Si existen y `forzar_con_citas == False`, devuelve la advertencia con la lista de pacientes afectados para que el médico decida si desea reprogramarlos o forzar el bloqueo (`forzar_con_citas = True`).

---

## 3. `configurar_horario_base_y_perfil`
Actualiza la jornada semanal recurrente del médico (`HORARIOS_BASE`), la duración de sus consultas, su tarifa base o la hora a la que desea recibir su resumen diario (`AGENDA_HOY` o `AGENDA_MANANA`).

* **Permiso requerido:** `agenda:configurar_perfil`
* **Parámetros expuestos al LLM:**
  * `dias_semana` (`list[int]`, opcional): Lista de días (`1`=Lunes ... `6`=Sábado, `0`=Domingo). Solo actualiza/reemplaza los días incluidos en esta lista, conservando intactos los demás días de la semana que el médico ya tuviera configurados.
  * `hora_apertura` (`str`, opcional): Formato `HH:MM`.
  * `hora_cierre` (`str`, opcional): Formato `HH:MM`.
  * `duracion_cita_minutos` (`int`, opcional): Ej. `30`, `45`, `60`.
  * `tarifa_base_consulta` (`float`, opcional): Precio por defecto de la consulta.
  * `hora_resumen_diario` (`str`, opcional): Hora en punto en formato `HH:00` (ej. `"08:00"`, `"12:00"`, `"20:00"`).
  * `alcance_resumen_diario` (`str`, opcional): `"AGENDA_HOY"` o `"AGENDA_MANANA"`.
* **Parámetros inyectados por Gateway:** `profesional_usuario_id = X-User-Id`

---

## 4. `agendar_o_mover_cita_manual`
Permite al médico agendar una cita directamente para un paciente (que llamó por teléfono o pidió hora en persona), registrar opcionalmente el motivo de la consulta, cambiar de hora una cita existente o cancelarla.

* **Permiso requerido:** `citas:gestion_manual_medico`
* **Parámetros expuestos al LLM:**
  * `accion` (`str`, requerido): `"CREAR"`, `"MOVER"` o `"CANCELAR"`.
  * `nombre_paciente` (`str`, opcional): Requerido al crear; también puede usarse en `"MOVER"` o `"CANCELAR"` para identificar la cita por el nombre del paciente si no se conoce `cita_id`.
  * `telefono_paciente` (`str`, opcional): Teléfono de contacto.
  * `cita_id` (`str`, opcional): UUID de la cita o nombre del paciente al mover o cancelar una cita existente.
  * `fecha_destino` (`str`, opcional): `YYYY-MM-DD` (requerido para `"CREAR"` y `"MOVER"`).
  * `hora_destino` (`str`, opcional): `HH:MM` (requerido para `"CREAR"` y `"MOVER"`).
  * `motivo_consulta` (`str | None`, opcional): Motivo o nota clínica breve indicada por el médico al agendar o actualizar la cita.
  * `monto_personalizado` (`float`, opcional): Si el médico desea fijar un precio distinto a su tarifa base desde la creación.
* **Parámetros inyectados por Gateway:** `profesional_usuario_id = X-User-Id`
* **Reglas de negocio:**
  * Valida que el horario destino en `"CREAR"` o `"MOVER"` no colisione con otra cita activa del médico (`SlotTakenError`).

---

## 5. `registrar_pago_o_descuento`
Registra un pago total, un abono parcial en la tabla `PAGOS` o aplica un descuento sobre `CITAS.monto_acordado`, recalculando automáticamente el `estado_financiero` (`PENDIENTE`, `PARCIAL`, `PAGADO`, `EXONERADO`).

* **Permiso requerido:** `finanzas:registrar_pago`
* **Parámetros expuestos al LLM:**
  * `cita_id` (`str`, requerido): UUID de la cita o nombre del paciente (resoluble automáticamente contra las citas del médico).
  * `monto_pagado` (`float`, opcional): Monto recibido en esta transacción (`0` si solo se está aplicando un descuento previo al pago).
  * `metodo_pago` (`str`, opcional): `"EFECTIVO"`, `"TRANSFERENCIA"`, `"PAGO_MOVIL"`, `"ZELLE"`, `"TARJETA"`.
  * `nuevo_monto_acordado` (`float`, opcional): Si el médico indica un descuento sobre el total de la consulta (ej. bajar de $50 a $35).
  * `referencia` (`str`, opcional): Número de comprobante.
* **Parámetros inyectados por Gateway:** `profesional_usuario_id = X-User-Id`

---

## 6. `consultar_resumen_ganancias`
Calcula el reporte financiero del médico en un periodo determinado (hoy, esta semana, este mes), desglosando dinero real ingresado en caja, saldo pendiente por cobrar de citas completadas e ingresos proyectados.

* **Permiso requerido:** `finanzas:ver_reportes`
* **Parámetros expuestos al LLM:**
  * `fecha_inicio` (`str`, requerido): `YYYY-MM-DD`.
  * `fecha_fin` (`str`, requerido): `YYYY-MM-DD`.
* **Parámetros inyectados por Gateway:** `profesional_usuario_id = X-User-Id`
* **Retorno esperado:**
  ```json
  {
    "success": true,
    "periodo": "2026-11-01 a 2026-11-07",
    "moneda": "USD",
    "total_cobrado_en_caja": 450.00,
    "desglose_por_metodo": {"TRANSFERENCIA": 300.00, "EFECTIVO": 150.00},
    "pendiente_por_cobrar_completadas": 90.00,
    "proyectado_citas_futuras": 225.00,
    "total_citas_atendidas": 10,
    "total_inasistencias": 1
  }
  ```