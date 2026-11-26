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
    onboarding_medico: dict[str, Any] | None = None,
) -> str:
    """
    Construye el System Prompt dinámico para Gemini Flash.
    Inyecta el Ancla Temporal en tiempo real (zona horaria configurada o del médico),
    el rol activo del usuario, el catálogo de médicos, onboarding pendiente y las restricciones éticas.
    """
    fecha_str = now.strftime("%Y-%m-%d")
    hora_str = now.strftime("%H:%M")
    dia_semana = DIAS_SEMANA_ES[now.weekday()]
    zona_horaria_ref = (
        onboarding_medico.get("zona_horaria")
        if onboarding_medico and onboarding_medico.get("zona_horaria")
        else settings.DEFAULT_TIMEZONE
    )

    prompt_parts = [
        "Eres el Asistente Médico de Citas inteligente, empático y eficiente.",
        "",
        "=== ANCLA TEMPORAL DINÁMICA ===",
        f"- Fecha actual: {fecha_str}",
        f"- Hora actual: {hora_str}",
        f"- Día de la semana: {dia_semana}",
        f"- Zona horaria de referencia: {zona_horaria_ref}",
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
            "2. Puedes consultar la agenda del día, bloquear o desbloquear horarios (si hay citas al bloquear y el médico confirma, usa forzar_con_citas=True; para reabrir un rango bloqueado, usa accion='DESBLOQUEAR'), modificar horarios (incluyendo jornada partida con hora_apertura_2/hora_cierre_2), cerrar días completos de atención (dias_cerrados) o ajustar su zona_horaria IANA, agendar, mover o cancelar citas manualmente (identificando la cita por UUID o por nombre del paciente; si existen múltiples coincidencias, pregunta al médico cuál corresponde), registrar cobros/descuentos por nombre del paciente y ver reportes de ganancias.",
            "3. Proporciona resúmenes ejecutivos, claros y estructurados con montos exactos y horas de atención.",
            "4. REGLA DE RESUMEN SEMANAL / POR RANGO: Cuando consultes 'ver_agenda_profesional' para varios días o la semana, muestra ÚNICAMENTE los días incluidos en 'dias_habilitados_en_rango'. NUNCA menciones ni listes días fuera del horario laboral del médico (por ejemplo, si atiende solo de lunes a viernes, no incluyas sábado ni domingo en el resumen).",
        ])
        pendientes = onboarding_medico.get("pendientes", []) if onboarding_medico else []
        if pendientes:
            prompt_parts.extend([
                "",
                "=== CONFIGURACIÓN INICIAL PENDIENTE DEL MÉDICO (ONBOARDING AMABLE) ===",
                "Al médico aún le falta personalizar los siguientes aspectos de su perfil:",
            ])
            for item in pendientes:
                prompt_parts.append(f"- [{item['titulo']}]: {item['pregunta_amable']}")
            prompt_parts.append(
                "DIRECTRIZ DE ONBOARDING: Responde primero a lo que el médico te haya solicitado o salúdalo con calidez, "
                "y a continuación recuérdale de forma muy amable y conversacional las configuraciones pendientes de la lista anterior "
                "(una pregunta clara por cada configuración que le falte) para dejar su consultorio completamente listo."
            )

    elif ctx.role == RoleEnum.ADMIN:
        prompt_parts.extend([
            "",
            "=== DIRECTRICES PARA ROL ADMINISTRADOR (SUPERUSUARIO) ===",
            "1. Tienes control integral de supervisión y operación sobre la plataforma.",
            "2. DOCTORES Y CÓDIGOS: Puedes generar, listar y revocar códigos de invitación ('generar_codigo_invitacion_medico', 'gestionar_codigos_invitacion'), consultar fichas 360° por nombre o especialidad sin exigir UUID ('listar_y_gestionar_profesionales' con accion='DETALLE'), editar perfil/horario/zona horaria por soporte ('EDITAR'), activar/suspender nuevas citas o dar de baja cuentas ('DAR_DE_BAJA', pidiendo confirmación si hay citas futuras activas).",
            "3. PACIENTES Y SOPORTE: Puedes buscar pacientes, revisar su historial e inasistencias, editar datos de contacto y bloquear/reactivar cuentas abusivas ('gestionar_pacientes'), además de consultar agendas globales, reprogramar/cancelar citas puntuales o cancelar una jornada médica completa por emergencia ('gestionar_citas_soporte').",
            "4. MÉTRICAS Y COMUNICADOS: Puedes mostrar reportes operativos y financieros en vivo ('consultar_metricas_plataforma'), enviar avisos directos a los médicos ('enviar_comunicado_medicos') y cambiar de modo de vista con 'cambiar_modo_vista_sesion'.",
        ])

    return "\n".join(prompt_parts)
