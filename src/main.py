import logging
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from src.core.config import get_settings
from src.core.exceptions import AppError
from src.api.v1 import api_v1_router

def _configure_app_logging() -> None:
    """Configura logs INFO únicamente para módulos propios (src.*) sin saturar con librerías HTTP."""
    app_logger = logging.getLogger("src")
    app_logger.setLevel(logging.INFO)
    if not app_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s\n")
        )
        app_logger.addHandler(handler)


_configure_app_logging()

settings = get_settings()

app = FastAPI(
    title="Asistente Médico de Citas (IA + Híbrido)",
    description="Microservicio backend para agendamiento médico, disponibilidad por excepción y control financiero",
    version="0.1.0",
)


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": exc.message,
            "code": exc.code,
        },
    )


app.include_router(api_v1_router)


@app.get("/")
async def root():
    return {
        "service": "asistente-citas",
        "docs": "/docs",
        "health": "/api/v1/health",
    }

