import os

from fastapi import FastAPI, HTTPException

app = FastAPI(title="API Gateway")

GATEWAY_TOKEN = os.getenv("GATEWAY_TOKEN", "token-clinica-123")
RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "30"))
BFF_URL = os.getenv("BFF_URL", "http://bff-web:8000")
ORQUESTADOR_URL = os.getenv("ORQUESTADOR_URL", "http://orquestador:8000")
UPSTREAM_TIMEOUT = float(os.getenv("UPSTREAM_TIMEOUT_SECONDS", "5"))


@app.get("/health")
def health():
    return {"status": "ok", "service": "api-gateway"}


@app.post("/api/citas")
def agendar_cita():
    raise HTTPException(status_code=501, detail="No implementado")


@app.get("/api/citas/{saga_id}")
def consultar_cita(saga_id: str):
    raise HTTPException(status_code=501, detail="No implementado")


@app.get("/api/bff/resumen-mascota/{mascota_id}")
def resumen_mascota(mascota_id: int):
    raise HTTPException(status_code=501, detail="No implementado")
