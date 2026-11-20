import os

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy.orm import Session

from . import models, schemas
from .database import Base, engine, get_db

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Pagos")

PASARELA_LATENCIA_MS = int(os.getenv("PASARELA_LATENCIA_MS", "0"))
PASARELA_FALLA = os.getenv("PASARELA_FALLA", "false").lower() == "true"


@app.get("/health")
def health():
    return {"status": "ok", "service": "pagos"}


def llamar_pasarela_externa(monto: int) -> str:
    raise NotImplementedError


@app.post("/pagos/cobrar", response_model=schemas.PagoOut)
def cobrar(datos: schemas.CobroIn, db: Session = Depends(get_db)):
    raise HTTPException(status_code=501, detail="No implementado")


@app.post("/pagos/{saga_id}/reembolsar", response_model=schemas.PagoOut)
def reembolsar(saga_id: str, db: Session = Depends(get_db)):
    raise HTTPException(status_code=501, detail="No implementado")


@app.get("/pagos/{saga_id}", response_model=schemas.PagoOut)
def obtener_pago(saga_id: str, db: Session = Depends(get_db)):
    raise HTTPException(status_code=501, detail="No implementado")


