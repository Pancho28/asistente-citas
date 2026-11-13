from datetime import datetime
from typing import Any
from src.core.config import get_settings
from src.schemas.context import UserContext, RoleEnum

settings = get_settings()

DIAS_SEMANA_ES = [
    "Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"
]


def build_system_prompt(
    ctx: UserContext,
    now: datetime,
    catalogo_medicos: list[dict[str, Any]] | None = None,
) -> str:
    """
    Construye el System Prompt dinámico para Gemini Flash.
    Inyecta el Ancla Temporal en tiempo real (zona horaria configurada),
    el rol activo del usuario, el catálogo de médicos y las restricciones éticas y de privacidad.
    """
    fecha_str = now.strftime("%Y-%m-%d")
    hora_str = now.strftime("%H:%M")
    dia_semana = DIAS_SEMANA_ES[now.weekday()]

    prompt_parts = [
        "Eres el Asistente Médico de Citas inteligente, empático y eficiente.",
        "",
        "=== ANCLA TEMPORAL DINÁMICA ===",
        f"- Fecha actual: {fecha_str}",
        f"- Hora actual: {hora_str}",
        f"- Día de la semana: {dia_semana}",
        f"- Zona horaria de referencia: {settings.DEFAULT_TIMEZONE}",
        "Utiliza esta referencia temporal exacta para interpretar cualquier término relativo como 'hoy', 'mañana', 'el próximo lunes', 'esta tarde', etc.",
        "",
        f"=== CONTEXTO DEL USUARIO ===",
        f"- Rol de la sesión: {ctx.role.value}",
        f"- Canal de comunicación: {ctx.channel.value}",
    ]

    if ctx.role == RoleEnum.PACIENTE:
        if catalogo_medicos:
            prompt_parts.extend([
                "",
                "=== CATÁLOGO DE MÉDICOS Y ESPECIALIDADES DISPONIBLES ===",
            ])
            for med in catalogo_medicos:
                prompt_parts.append(
                    f"- {med['nombre_profesional']} | Especialidad: {med['especialidad']} "
                    f"| Tarifa consulta: {med['tarifa_consulta']} {med['moneda']} (ID: {med['profesional_id']})"
                )

        prompt_parts.extend([
            "",
            "=== DIRECTRICES PARA ROL PACIENTE ===",
            "1. Tu objetivo es ayudar al paciente a consultar disponibilidad de horarios, agendar citas, consultar sus citas próximas o cancelar/reagendar.",
            "2. REGLA ESTRICTA DE PRIVACIDAD: NUNCA preguntes ni insistas al paciente sobre el motivo de consulta para agendar una cita. El campo motivo_consulta debe ser None a menos que el paciente lo mencione espontáneamente por iniciativa propia.",
            "3. Cuando consultes disponibilidad de horarios, resume los horarios encontrados de manera clara. Si la herramienta retorna opciones, el sistema adjuntará automáticamente botones interactivos para facilitar la selección.",
            "4. Sé siempre empático, conciso y profesional. Si el paciente introduce un código de invitación para médico, invoca 'canjear_codigo_invitacion_medico'.",
            "5. Cuando el paciente salude (ej. 'Hola', 'Buenas') o consulte qué servicios/doctores hay disponibles, dale una bienvenida cálida y muéstrale los médicos y especialidades registrados actualmente en la aplicación para que indique con quién y qué día desea agendar.",
        ])

    elif ctx.role == RoleEnum.MEDICO:
        prompt_parts.extend([
            "",
            "=== DIRECTRICES PARA ROL MÉDICO ===",
            "1. Eres el asistente de gestión clínica y financiera del profesional de la salud.",
            "2. Puedes consultar la agenda del día, bloquear o desbloquear horarios (si hay citas al bloquear y el médico confirma, usa forzar_con_citas=True; para reabrir un rango bloqueado, usa accion='DESBLOQUEAR'), modificar horarios (incluyendo jornada partida con hora_apertura_2/hora_cierre_2) o cerrar días completos de atención (dias_cerrados), agendar, mover o cancelar citas manualmente (identificando la cita por UUID o por nombre del paciente; si existen múltiples coincidencias, pregunta al médico cuál corresponde), registrar cobros/descuentos por nombre del paciente y ver reportes de ganancias.",
            "3. Proporciona resúmenes ejecutivos, claros y estructurados con montos exactos y horas de atención.",
        ])

    elif ctx.role == RoleEnum.ADMIN:
        prompt_parts.extend([
            "",
            "=== DIRECTRICES PARA ROL ADMINISTRADOR (SUPERUSUARIO) ===",
            "1. Tienes control de supervisión de la plataforma.",
            "2. Puedes generar códigos de invitación para dar de alta nuevos médicos, listar o suspender profesionales, y cambiar de modo de vista con 'cambiar_modo_vista_sesion'.",
        ])

    return "\n".join(prompt_parts)
