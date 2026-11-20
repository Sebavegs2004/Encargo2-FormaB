from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy.orm import Session

from . import models, schemas
from .database import Base, engine, get_db

Base.metadata.create_all(bind=engine)  

app = FastAPI(title="Agenda")


@app.get("/health")
def health():
    return {"status": "ok", "service": "agenda"}


@app.get("/slots", response_model=list[schemas.SlotOut])
def listar_slots(db: Session = Depends(get_db)):
    raise HTTPException(status_code=501, detail="No implementado")


@app.get("/slots/{slot_id}", response_model=schemas.SlotOut)
def obtener_slot(slot_id: int, db: Session = Depends(get_db)):
    raise HTTPException(status_code=501, detail="No implementado")


@app.post("/reservas", response_model=schemas.ReservaOut)
def reservar(datos: schemas.ReservaIn, db: Session = Depends(get_db)):
    raise HTTPException(status_code=501, detail="No implementado")


@app.post("/reservas/{saga_id}/liberar", response_model=schemas.ReservaOut)
def liberar(saga_id: str, db: Session = Depends(get_db)):
    raise HTTPException(status_code=501, detail="No implementado")

