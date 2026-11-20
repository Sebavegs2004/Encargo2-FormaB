import os

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy.orm import Session

from . import models, schemas
from .database import Base, engine, get_db

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Ficha")

FICHA_FALLA_CONFIRMAR = os.getenv("FICHA_FALLA_CONFIRMAR", "false").lower() == "true"


@app.get("/health")
def health():
    return {"status": "ok", "service": "ficha"}


@app.get("/mascotas/{mascota_id}", response_model=schemas.MascotaOut)
def obtener_mascota(mascota_id: int, db: Session = Depends(get_db)):
    raise HTTPException(status_code=501, detail="No implementado")


@app.get("/mascotas/{mascota_id}/citas", response_model=list[schemas.CitaOut])
def citas_de_mascota(mascota_id: int, db: Session = Depends(get_db)):
    raise HTTPException(status_code=501, detail="No implementado")


@app.post("/citas/confirmar", response_model=schemas.CitaOut)
def confirmar(datos: schemas.ConfirmarIn, db: Session = Depends(get_db)):
    raise HTTPException(status_code=501, detail="No implementado")


@app.post("/citas/{saga_id}/anular", response_model=schemas.CitaOut)
def anular(saga_id: str, db: Session = Depends(get_db)):
    raise HTTPException(status_code=501, detail="No implementado")
