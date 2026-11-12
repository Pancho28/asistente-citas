import asyncio
from sqlalchemy import select
from src.core.database import AsyncSessionLocal
from src.models.appointment import Cita
from src.models.user import Usuario, PerfilProfesional


async def fix_data() -> None:
    async with AsyncSessionLocal() as db:
        # 1. Obtener al Dr. Juan Pérez (777001)
        perez_user = (
            await db.execute(select(Usuario).where(Usuario.external_chat_id == "777001"))
        ).scalar_one_or_none()
        if not perez_user:
            print("No se encontró al Dr. Juan Pérez en la BD local.")
            return

        perez_perfil = (
            await db.execute(
                select(PerfilProfesional).where(PerfilProfesional.usuario_id == perez_user.id)
            )
        ).scalar_one_or_none()
        if not perez_perfil:
            print("No se encontró el perfil profesional del Dr. Juan Pérez.")
            return

        # 2. Reasignar la cita de María Alejandra Gómez al Dr. Juan Pérez
        maria_cita = (
            await db.execute(
                select(Cita).where(Cita.id == "ee0e32ba-c1b0-4fba-9dc4-003f2be82b79")
            )
        ).scalar_one_or_none()
        if maria_cita:
            print(f"Reasignando cita {maria_cita.id} de {maria_cita.profesional_id} al Dr. Pérez ({perez_perfil.id})")
            maria_cita.profesional_id = perez_perfil.id
            await db.commit()
            print("¡Cita de María reasignada exitosamente al Dr. Pérez!")
        else:
            print("Cita de María ya actualizada o no encontrada.")

        # 3. Desactivar el médico residual de prueba demo_doctor_1 (Dr. Carlos Mendoza)
        carlos_user = (
            await db.execute(select(Usuario).where(Usuario.external_chat_id == "demo_doctor_1"))
        ).scalar_one_or_none()
        if carlos_user:
            carlos_perfil = (
                await db.execute(
                    select(PerfilProfesional).where(PerfilProfesional.usuario_id == carlos_user.id)
                )
            ).scalar_one_or_none()
            if carlos_perfil:
                carlos_perfil.acepta_nuevas_citas = False
            carlos_user.activo = False
            await db.commit()
            print("Dr. Carlos Mendoza desactivado para evitar colisiones.")


if __name__ == "__main__":
    try:
        asyncio.run(fix_data())
    except Exception as exc:
        print("Aviso: No se pudo conectar a PostgreSQL local (Docker detenido):", exc)

