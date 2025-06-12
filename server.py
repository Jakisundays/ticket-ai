# Standard library imports
import asyncio
import logging

# Third-party imports
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.routing import APIRoute

# Local application imports
from routes.process_invoice import orchestrator
from routes.process_invoice import router as process_invoice_router
from routes.process_invoice_google import router as process_invoice_google_router

# Configuración del logger
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler()
    ]
)
app_logger = logging.getLogger("app_logger")

# Configuración básica de la API - título, versión y docs
app = FastAPI(
    title="API de Invoicy",
    description="API para procesar facturas electrónicas e imágenes de comprobantes utilizando Claude AI y otras utilidades. Documentación completa en español para facilitar la integración y el uso.",
    version="1.0.0",
    docs_url="/docs",  # URL para la doc swagger
    redoc_url="/redoc",  # URL para la doc redoc
    openapi_tags=[
        {
            "name": "General",
            "description": "Endpoints generales para chequeo de salud y bienvenida.",  # endpoints básicos
        },
        {
            "name": "Procesamiento de facturas",
            "description": "Procesamiento inteligente de facturas electrónicas e imágenes de comprobantes.",  # endpoints para facturas
        },
    ],
)

# Middleware para logging de solicitudes
@app.middleware("http")
async def log_requests(request: Request, call_next):
    app_logger.info(f"Solicitud entrante: {request.method} {request.url}")
    response = await call_next(request)
    app_logger.info(f"Respuesta saliente: {request.method} {request.url} - Estado: {response.status_code}")
    return response

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Conecta las rutas de facturas a la app
app.include_router(process_invoice_router)
app.include_router(process_invoice_google_router)


@app.on_event("startup")
async def startup_event():
    # Crea 5 workers al iniciar para procesar facturas en paralelo
    for _ in range(5):
        asyncio.create_task(orchestrator.worker())


# API Endpoints
@app.get(
    "/",
    summary="Chequeo de salud",
    tags=["General"],
    response_description="Mensaje de bienvenida y estado de la API.",
)
async def read_root():
    app_logger.info("Acceso al endpoint raíz.")
    return {"message": "Bienvenido a la API de Invoicy. 🥸"}
