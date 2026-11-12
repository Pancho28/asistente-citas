# admin_skills.md — Herramientas Exclusivas del Rol ADMIN (Superusuario)

**Módulo de implementación:** `src/agent/mcp_tools/admin.py`  
**Rol requerido:** `ADMIN`

---

## 1. `generar_codigo_invitacion_medico`
Crea una frase o código único de un solo uso en la tabla `CODIGOS_INVITACION` con tiempo de expiración para dar de alta a un nuevo profesional en el bot.

* **Permiso requerido:** `admin:generar_invitacion`
* **Parámetros expuestos al LLM:**
  * `especialidad_asignada` (`str`, requerido): Especialidad que tendrá el médico al canjear el código (ej. `"Psicología"`, `"Odontología"`).
  * `horas_validez` (`int`, opcional): Horas antes de que expire el código (por defecto `48`).
  * `frase_personalizada` (`str`, opcional): Si el administrador desea definir una frase específica; si se omite, el sistema genera un código seguro tipo `MED-PSICO-9481`.
* **Parámetros inyectados por Gateway:** `creado_por_admin_id = X-User-Id`
* **Retorno esperado:**
  ```json
  {
    "success": true,
    "frase_codigo": "MED-PSICO-9481",
    "especialidad": "Psicología",
    "expira_en": "2026-11-07T22:45:00Z",
    "link_directo_telegram": "[https://t.me/NombreDelBot?start=MED-PSICO-9481](https://t.me/NombreDelBot?start=MED-PSICO-9481)"
  }
  ```

---

## 2. `listar_y_gestionar_profesionales`
Permite al administrador listar todos los médicos registrados en el sistema, ver su estado de actividad o suspender/activar la recepción de nuevas citas de un profesional.

* **Permiso requerido:** `admin:gestionar_profesionales`
* **Parámetros expuestos al LLM:**
  * `accion` (`str`, requerido): `"LISTAR"`, `"ACTIVAR"` o `"SUSPENDER"`.
  * `profesional_id` (`str`, opcional): Requerido solo para `"ACTIVAR"` o `"SUSPENDER"`.
* **Parámetros inyectados por Gateway:** `admin_id = X-User-Id`

---

## 3. `cambiar_modo_vista_sesion`
Herramienta **exclusiva del superusuario (`ADMIN`)** para alternar su campo `USUARIOS.modo_vista_actual` y simular la experiencia exacta del bot como si fuera un paciente o un médico desde su mismo teléfono.

* **Permiso requerido:** `admin:cambiar_vista` (Exclusivo para `USUARIOS.rol_sistema == 'ADMIN'`)
* **Parámetros expuestos al LLM:**
  * `nuevo_modo` (`str`, requerido): `"ADMIN"`, `"MEDICO"` o `"PACIENTE"`.
* **Parámetros inyectados por Gateway:** `admin_id = X-User-Id`
* **Reglas de negocio:**
  * Verifica en base de datos que el usuario tenga `rol_sistema == 'ADMIN'` real (incluso si actualmente está viendo el bot en modo `PACIENTE` o `MEDICO`).
  * Ningún usuario con `rol_sistema == 'MEDICO'` o `'PACIENTE'` puede ejecutar esta herramienta ni los comandos rápidos `/modo_admin`, `/modo_medico` o `/modo_paciente`.