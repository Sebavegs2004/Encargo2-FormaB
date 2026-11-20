import os

from fastapi import FastAPI, HTTPException

app = FastAPI(title="BFF Web")

AGENDA_URL = os.getenv("AGENDA_URL", "http://agenda:8000")
PAGOS_URL = os.getenv("PAGOS_URL", "http://pagos:8000")
FICHA_URL = os.getenv("FICHA_URL", "http://ficha:8000")
TIMEOUT = float(os.getenv("UPSTREAM_TIMEOUT_SECONDS", "2"))

@app.get("/health")
def health():
    return {"status": "ok", "service": "bff-web"}

@app.get("/resumen-mascota/{mascota_id}")
def resumen_mascota(mascota_id: int):
    raise HTTPException(status_code=501, detail="No implementado")
