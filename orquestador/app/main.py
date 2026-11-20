import os

from fastapi import Depends, FastAPI, Header, HTTPException
from sqlalchemy.orm import Session

from . import models, schemas
from .database import Base, engine, get_db

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Orquestador")

AGENDA_URL = os.getenv("AGENDA_URL", "http://agenda:8000")
PAGOS_URL = os.getenv("PAGOS_URL", "http://pagos:8000")
FICHA_URL = os.getenv("FICHA_URL", "http://ficha:8000")

TIMEOUT_AGENDA = float(os.getenv("TIMEOUT_AGENDA_SECONDS", "2"))
TIMEOUT_PAGOS = float(os.getenv("TIMEOUT_PAGOS_SECONDS", "2"))
TIMEOUT_FICHA = float(os.getenv("TIMEOUT_FICHA_SECONDS", "2"))


@app.get("/health")
def health():
    return {"status": "ok", "service": "orquestador"}


@app.post("/citas", response_model=schemas.SagaOut)
def agendar_consulta(
    datos: schemas.AgendarIn,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    db: Session = Depends(get_db),
):
    raise HTTPException(status_code=501, detail="No implementado")


@app.get("/citas/{saga_id}", response_model=schemas.SagaOut)
def consultar_saga(saga_id: str, db: Session = Depends(get_db)):
    raise HTTPException(status_code=501, detail="No implementado")


