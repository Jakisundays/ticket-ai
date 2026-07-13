import asyncio
import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from utils.rate_limit import limiter

app_logger = logging.getLogger("app_logger")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()],
)


def create_app(*, title: str, description: str, routers, extra_workers=None) -> FastAPI:
    """
    Fábrica compartida por los entrypoints delgados (server_wa.py, server_bas.py,
    server_core.py) para no duplicar/desincronizar el setup de CORS, logging y
    health check entre los 3.

    `extra_workers`: orquestadores que necesitan workers adicionales al que ya se
    autoarranca en su propio __init__. Replica exactamente el patrón de server.py,
    que hoy suma 5 workers extra para el flujo Claude y el flujo Gemini/wa, pero
    NINGUNO para el flujo Gemini+BAS (su único worker es el que arranca solo).
    Pasar `None` (o lista vacía) para servicios que no necesitan el refuerzo.
    """
    app = FastAPI(
        title=title,
        description=description,
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_tags=[
            {
                "name": "General",
                "description": "Endpoints generales para chequeo de salud.",
            },
            {
                "name": "Procesamiento de facturas",
                "description": "Procesamiento inteligente de facturas electrónicas e imágenes de comprobantes.",
            },
        ],
    )

    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        app_logger.info(f"Solicitud entrante: {request.method} {request.url}")
        response = await call_next(request)
        app_logger.info(
            f"Respuesta saliente: {request.method} {request.url} - Estado: {response.status_code}"
        )
        return response

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    for router in routers:
        app.include_router(router)

    if extra_workers:

        @app.on_event("startup")
        async def startup_event():
            for orchestrator in extra_workers:
                for _ in range(5):
                    asyncio.create_task(orchestrator.worker())

    @app.get("/health", tags=["General"])
    async def health():
        return {"status": "ok"}

    @app.get("/", tags=["General"])
    async def read_root():
        app_logger.info("Acceso al endpoint raíz.")
        return {"message": f"{title} — OK"}

    return app
