from fastapi import APIRouter, Request
from datetime import datetime
import json
import os

router = APIRouter(prefix="/webhook")
WEBHOOK_FILE = "webhooks.json"


@router.post("")
async def webhook_receiver(request: Request):
    payload = await request.json()

    data = []
    if os.path.exists(WEBHOOK_FILE):
        try:
            with open(WEBHOOK_FILE, "r") as f:
                if os.path.getsize(WEBHOOK_FILE) > 0:
                    data = json.load(f)
        except json.JSONDecodeError:
            data = []

    data.append({"timestamp": datetime.utcnow().isoformat(), "payload": payload})

    with open(WEBHOOK_FILE, "w") as f:
        json.dump(data, f, indent=2)

    return {"status": "ok", "received": payload}


@router.get("")
def get_webhooks():
    if os.path.exists(WEBHOOK_FILE):
        if os.path.getsize(WEBHOOK_FILE) == 0:
            return []
        with open(WEBHOOK_FILE, "r") as f:
            data = json.load(f)

        # Ordenar por timestamp descendente (más reciente primero)
        data_sorted = sorted(
            data, key=lambda x: datetime.fromisoformat(x["timestamp"]), reverse=True
        )
        return data_sorted

    return {"error": "No webhooks yet"}
