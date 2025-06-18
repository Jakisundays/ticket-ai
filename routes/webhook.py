from fastapi import APIRouter, Request
from datetime import datetime
import json
import os

router = APIRouter(prefix="/webhook")
WEBHOOK_FILE = "webhooks.json"


@router.post("/")
async def webhook_receiver(request: Request):
    payload = await request.json()

    # Cargar contenido existente o crear lista vacía
    if os.path.exists(WEBHOOK_FILE):
        with open(WEBHOOK_FILE, "r") as f:
            data = json.load(f)
    else:
        data = []

    # Agregar nuevo webhook con timestamp
    data.append({"timestamp": datetime.utcnow().isoformat(), "payload": payload})

    # Guardar actualizado
    with open(WEBHOOK_FILE, "w") as f:
        json.dump(data, f, indent=2)

    return {"status": "ok", "received": payload}


@router.get("/")
def get_webhooks():
    if os.path.exists(WEBHOOK_FILE):
        with open(WEBHOOK_FILE, "r") as f:
            data = json.load(f)
        return data
    return {"error": "No webhooks yet"}
