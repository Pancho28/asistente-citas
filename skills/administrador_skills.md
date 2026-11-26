# administrador_skills.md — Herramientas Exclusivas del Rol ADMIN (Superusuario)

**Módulo de implementación:** `src/agent/mcp_tools/admin.py`  
**Capa de negocio:** `src/services/admin_service.py` y `src/services/onboarding_service.py`  
**Rol requerido:** `ADMIN`

---

## 1. `generar_codigo_invitacion_medico`
Crea una frase o código único de un solo uso en la tabla `CODIGOS_INVITACION` con tiempo de expiración para dar de alta a un nuevo profesional en el bot.

* **Permiso requerido:** `admin:generar_invitacion`
* **Parámetros expuestos al LLM:**
  * `especialidad_asignada` (`str`, requerido): Especialidad que tendrá el médico al canjear el código (ej. `"Psicología"`, `"Odontología"`).
  * `horas_validez` (`int`, opcional): Horas antes de que expire el código (por defecto `48`).
  * `frase_personalizada` (`str`, opcional): Si el administrador desea definir una frase específica; si se omite, genera un código seguro tipo `MED-PSIC-9481`.
* **Parámetros inyectados por Gateway:** `creado_por_admin_id = X-User-Id`

---

## 2. `gestionar_codigos_invitacion`
Permite consultar el estado de los códigos de invitación emitidos (vigentes, usados o expirados) o revocar/anular de inmediato un código enviado por error antes de que sea canjeado.

* **Permiso requerido:** `admin:generar_invitacion`
* **Parámetros expuestos al LLM:**
  * `accion` (`str`, requerido): `"LISTAR"` o `"REVOCAR"`.
  * `filtro_estado` (`str`, opcional, default `"VIGENTES"`): `"VIGENTES"`, `"USADOS"` o `"TODOS"` (para `"LISTAR"`).
  * `frase_codigo` (`str`, opcional): Requerido para `"REVOCAR"` (ej. `"MED-CARD-4821"`).
* **Parámetros inyectados por Gateway:** `admin_id = X-User-Id`

---

## 3. `listar_y_gestionar_profesionales`
Permite listar todos los médicos, consultar la ficha 360° de un doctor buscando por nombre/especialidad o UUID (sin exigir UUID manual), editar su perfil/horario/zona horaria por soporte, pausar/activar nuevas citas o dar de baja/reactivar su cuenta.

* **Permiso requerido:** `admin:gestionar_profesionales`
* **Parámetros expuestos al LLM:**
  * `accion` (`str`, requerido): `"LISTAR"`, `"DETALLE"`, `"ACTIVAR"`, `"SUSPENDER"`, `"EDITAR"`, `"DAR_DE_BAJA"` o `"REACTIVAR_CUENTA"`.
  * `profesional_id` (`str`, opcional): UUID del perfil o usuario médico, o directamente el **nombre / especialidad** del médico (ej. `"Dr. Pérez"`, `"Cardiología"`).
  * `nuevo_nombre` (`str`, opcional): Para `"EDITAR"`.
  * `nuevo_telefono` (`str`, opcional): Para `"EDITAR"`.
  * `nueva_especialidad` (`str`, opcional): Para `"EDITAR"`.
  * `zona_horaria` (`str`, opcional): Zona horaria IANA o alias para `"EDITAR"`.
  * `tarifa_base_consulta` (`float`, opcional): Para `"EDITAR"`.
  * `duracion_cita_minutos` (`int`, opcional): Para `"EDITAR"`.
  * `dias_semana` (`list[int]`, opcional): Días laborales (`1`=Lun...`6`=Sáb, `0`=Dom) para `"EDITAR"` horario en nombre del médico.
  * `hora_apertura` / `hora_cierre` (`str`, opcional): `HH:MM` para `"EDITAR"`.
  * `forzar` (`bool`, opcional, default `False`): En `"DAR_DE_BAJA"`, si el médico tiene citas futuras activas y `forzar=False`, advierte primero con el conteo de citas afectadas.
* **Parámetros inyectados por Gateway:** `admin_id = X-User-Id`

---

## 4. `gestionar_pacientes`
Directorio y gestión administrativa de pacientes: búsqueda flexible, historial de citas/inasistencias, corrección de datos de contacto y bloqueo/reactivación de cuentas (`USUARIOS.activo`).

* **Permiso requerido:** `admin:gestionar_pacientes`
* **Parámetros expuestos al LLM:**
  * `accion` (`str`, requerido): `"BUSCAR"`, `"DETALLE"`, `"EDITAR"`, `"BLOQUEAR"` o `"REACTIVAR"`.
  * `identificador_paciente` (`str`, opcional): Nombre, teléfono, `external_chat_id` o UUID del paciente (opcional en `"BUSCAR"` para listar los más recientes).
  * `nuevo_nombre` (`str`, opcional): Para `"EDITAR"`.
  * `nuevo_telefono` (`str`, opcional): Para `"EDITAR"`.
  * `zona_horaria` (`str`, opcional): Para `"EDITAR"`.
* **Reglas de seguridad:**
  * Nunca permite bloquear a un usuario con `rol_sistema == 'ADMIN'`.
  * Cuando `USUARIOS.activo == False`, el webhook rechaza cualquier interacción (botones o texto) sin consumir tokens de IA.

---

## 5. `gestionar_citas_soporte`
Mesa de ayuda administrativa para consultar citas globales, cancelar o reprogramar una cita puntual, o cancelar/bloquear la jornada completa de un médico ante una emergencia notificando automáticamente a los pacientes afectados.

* **Permiso requerido:** `admin:soporte_citas`
* **Parámetros expuestos al LLM:**
  * `accion` (`str`, requerido): `"CONSULTAR"`, `"CANCELAR_CITA"`, `"REPROGRAMAR_CITA"` o `"CANCELAR_JORNADA_MEDICO"`.
  * `cita_id` (`str`, opcional): UUID de la cita (para `"CANCELAR_CITA"` o `"REPROGRAMAR_CITA"`).
  * `identificador_medico` (`str`, opcional): Nombre, especialidad o UUID del médico.
  * `identificador_paciente` (`str`, opcional): Nombre, teléfono o UUID del paciente.
  * `fecha_inicio` (`str`, opcional): `YYYY-MM-DD` (fecha de consulta o día a cancelar en `"CANCELAR_JORNADA_MEDICO"`).
  * `fecha_fin` (`str`, opcional): `YYYY-MM-DD` (para rango en `"CONSULTAR"`).
  * `nueva_fecha` (`str`, opcional): `YYYY-MM-DD` (requerido en `"REPROGRAMAR_CITA"`).
  * `nueva_hora` (`str`, opcional): `HH:MM` (requerido en `"REPROGRAMAR_CITA"`).
  * `motivo` (`str`, opcional): Motivo informado en la notificación a paciente/médico.

---

## 6. `consultar_metricas_plataforma`
Obtiene el pulso operativo y financiero en tiempo real del SaaS: médicos activos vs. onboarding pendiente, pacientes activos/bloqueados/nuevos, desglose de citas por estado, tasa de inasistencias e ingresos cobrados/proyectados.

* **Permiso requerido:** `admin:ver_metricas`
* **Parámetros expuestos al LLM:**
  * `periodo` (`str`, opcional, default `"HOY"`): `"HOY"`, `"SEMANA"`, `"MES"` o `"PERSONALIZADO"`.
  * `fecha_inicio` (`str`, opcional): `YYYY-MM-DD` (para `"PERSONALIZADO"`).
  * `fecha_fin` (`str`, opcional): `YYYY-MM-DD` (para `"PERSONALIZADO"`).

---

## 7. `enviar_comunicado_medicos`
Envía desde el propio bot un anuncio o recordatorio directo por Telegram/WhatsApp a todos los médicos activos, solo a los que tienen onboarding incompleto o a un médico específico.

* **Permiso requerido:** `admin:enviar_comunicados`
* **Parámetros expuestos al LLM:**
  * `mensaje` (`str`, requerido): Contenido del aviso a enviar.
  * `destinatario` (`str`, opcional, default `"TODOS_ACTIVOS"`): `"TODOS_ACTIVOS"`, `"ONBOARDING_PENDIENTE"` o nombre/UUID de un médico puntual.

---

## 8. `cambiar_modo_vista_sesion`
Herramienta **exclusiva del superusuario (`ADMIN`)** para alternar su campo `USUARIOS.modo_vista_actual` (`"ADMIN"`, `"MEDICO"`, `"PACIENTE"`) y simular la experiencia exacta de otros roles.

* **Permiso requerido:** `admin:cambiar_vista` (Exclusivo para `USUARIOS.rol_sistema == 'ADMIN'`)
