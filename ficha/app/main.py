"""Servicio Ficha: confirma y anula la cita en la ficha de la mascota (Paso 3 de la Saga)."""
import os

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from . import models, schemas
from .database import Base, SessionLocal, engine, get_db

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Ficha")

# Variable de MODULO (no constante): /admin/fallo la reasigna sin reiniciar el contenedor.
FICHA_FALLA_CONFIRMAR = os.getenv("FICHA_FALLA_CONFIRMAR", "false").lower() == "true"

# Mascotas de ejemplo para probar la saga sin cargar datos a mano.
# Solo se insertan si la tabla esta vacia: un reinicio no debe duplicarlas.
MASCOTAS_INICIALES = [
    {"nombre": "Luna", "especie": "Perro", "duenio": "Camila"},
    {"nombre": "Michi", "especie": "Gato", "duenio": "Tomas"},
    {"nombre": "Coco", "especie": "Perro", "duenio": "Javiera"},
]


class FalloIn(BaseModel):
    """Cuerpo de /admin/fallo. Se define aqui porque es propio de este servicio."""

    activa: bool = False  # True => confirmar() responde 500 a proposito


@app.on_event("startup")
def sembrar_mascotas():
    """Carga las mascotas de ejemplo la primera vez que arranca el servicio."""
    db = SessionLocal()
    try:
        if db.query(models.Mascota).count() == 0:
            db.add_all([models.Mascota(**datos) for datos in MASCOTAS_INICIALES])
            db.commit()
    finally:
        # Cerramos la sesion siempre, tambien si la insercion falla.
        db.close()


@app.get("/health")
def health():
    return {"status": "ok", "service": "ficha"}


@app.get("/mascotas/{mascota_id}", response_model=schemas.MascotaOut)
def obtener_mascota(mascota_id: int, db: Session = Depends(get_db)):
    mascota = db.get(models.Mascota, mascota_id)
    if mascota is None:
        raise HTTPException(status_code=404, detail="Mascota no encontrada")
    return mascota


@app.get("/mascotas/{mascota_id}/citas", response_model=list[schemas.CitaOut])
def citas_de_mascota(mascota_id: int, db: Session = Depends(get_db)):
    # Verificamos la mascota para distinguir "no existe" de "no tiene citas".
    mascota = db.get(models.Mascota, mascota_id)
    if mascota is None:
        raise HTTPException(status_code=404, detail="Mascota no encontrada")

    return (
        db.query(models.CitaFicha)
        .filter(models.CitaFicha.mascota_id == mascota_id)
        .order_by(models.CitaFicha.id)
        .all()
    )


@app.post("/citas/confirmar", response_model=schemas.CitaOut)
def confirmar(datos: schemas.ConfirmarIn, db: Session = Depends(get_db)):
    """PASO 3 de la Saga: confirmar la cita en la ficha.

    Un mismo saga_id nunca debe producir dos citas. La falla inducida se dispara
    ANTES de escribir, para que la compensacion tenga un estado limpio que revertir.
    """
    cita = (
        db.query(models.CitaFicha)
        .filter(models.CitaFicha.saga_id == datos.saga_id)
        .first()
    )
    if cita is not None:
        # Reintento: devolvemos la cita existente y no ejecutamos nada de nuevo.
        return cita

    mascota = db.get(models.Mascota, datos.mascota_id)
    if mascota is None:
        # No se puede confirmar una cita para una mascota inexistente.
        raise HTTPException(status_code=404, detail="Mascota no encontrada")

    if FICHA_FALLA_CONFIRMAR:
        # print con flush=True: con PYTHONUNBUFFERED=1 queda en los logs del contenedor
        # y sirve como evidencia de que la falla fue inducida, no un error real.
        print(f"[Ficha] Falla inducida al confirmar saga {datos.saga_id}", flush=True)
        raise HTTPException(status_code=500, detail="Falla inducida en Ficha")

    cita = models.CitaFicha(
        saga_id=datos.saga_id,
        mascota_id=datos.mascota_id,
        slot_id=datos.slot_id,
        estado="CONFIRMADA",
    )
    db.add(cita)
    db.commit()
    db.refresh(cita)
    return cita


@app.post("/citas/{saga_id}/anular", response_model=schemas.CitaOut)
def anular(saga_id: str, db: Session = Depends(get_db)):
    """COMPENSACION del Paso 3: anular la cita."""
    cita = (
        db.query(models.CitaFicha)
        .filter(models.CitaFicha.saga_id == saga_id)
        .first()
    )

    if cita is None:
        # La saga pudo fallar antes de confirmar: la compensacion no devuelve 404.
        return schemas.CitaOut(
            id=0, saga_id=saga_id, mascota_id=0, slot_id=0, estado="ANULADA"
        )

    if cita.estado == "ANULADA":
        # Ya se anulo: repetir la compensacion no cambia nada.
        return cita

    cita.estado = "ANULADA"
    db.commit()
    db.refresh(cita)
    return cita


@app.post("/admin/fallo")
def configurar_fallo(datos: FalloIn):
    """Activa/desactiva la falla de confirmar() en caliente."""
    # 'global' porque reasignamos la variable que lee confirmar().
    global FICHA_FALLA_CONFIRMAR
    FICHA_FALLA_CONFIRMAR = datos.activa
    return {"activa": FICHA_FALLA_CONFIRMAR}
