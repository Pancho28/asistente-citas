# SKILLS.md — Arquitectura de Herramientas (MCP / Function Calling) y Seguridad RBAC

## 1. Propósito de este Directorio
Este directorio define el contrato estricto de todas las herramientas (*Tools*) que el Orquestador expone a **Gemini Flash**. Ninguna función puede implementarse en `src/agent/mcp_tools/` sin estar especificada previamente en los archivos de esta carpeta.

### Mapa de Archivos de Skills
| Archivo de Especificación | Módulo Python Destino | Rol Requerido (`X-User-Role`) |
| :--- | :--- | :--- |
| `skills/paciente_skills.md` | `src/agent/mcp_tools/patient.py` | `PACIENTE` (o `ADMIN` usando `/modo_paciente`) |
| `skills/medico_skills.md` | `src/agent/mcp_tools/doctor.py` | `MEDICO` (o `ADMIN` usando `/modo_medico`) |
| `skills/admin_skills.md` | `src/agent/mcp_tools/admin.py` | `ADMIN` (Exclusivo Superusuario) |

---

## 2. Reglas Globales de Seguridad (Defensa en Profundidad)

### 2.1 Doble Barrera de Autorización (RBAC / PBAC)
1. **Barrera 1 — Filtrado Dinámico de Contexto (Pre-LLM):**
   Antes de invocar a `client.models.generate_content()`, el registro (`src/agent/mcp_tools/registry.py`) lee las cabeceras inyectadas por el API Gateway (`X-User-Role` y `X-User-Permissions`) y construye la lista `tools=[...]` incluyendo **únicamente** las funciones autorizadas para ese rol. El modelo jamás conoce la existencia de herramientas de roles superiores.
2. **Barrera 2 — Validación en Tiempo de Ejecución (Post-LLM):**
   Cada función de herramienta valida mediante un decorador `@require_permission("permiso:accion")` que el `UserContext` activo posea el permiso antes de tocar PostgreSQL o Redis. Si falla, levanta `PermissionDeniedError` y registra una alerta de seguridad.

### 2.2 Inyección Silenciosa de Identidad (Anti-IDOR)
* **PROHIBIDO** pedirle a Gemini Flash que envíe el `paciente_id`, `medico_id` o `admin_id` del usuario actual como argumento de una herramienta.
* El LLM solo genera argumentos de negocio (`fecha`, `hora`, `monto`).
* El ejecutor MCP inyecta automáticamente el `user_id` verificado proveniente de la cabecera `X-User-Id` del API Gateway.

### 2.3 Estándar de Respuesta de las Tools
Todas las herramientas deben devolver un diccionario serializable a JSON con una estructura predecible para que Gemini Flash redacte la respuesta final sin alucinar:
```json
{
  "success": true,
  "code": "SLOT_AVAILABLE",
  "data": { ... },
  "ui_hint": "SHOW_BOOKING_BUTTONS"
}
```
*Nota sobre `ui_hint`:* Cuando una herramienta devuelve `"ui_hint": "SHOW_BOOKING_BUTTONS"`, el adaptador de salida adjunta automáticamente los botones interactivos (`callback_data`) correspondientes a los horarios listados en `data`.