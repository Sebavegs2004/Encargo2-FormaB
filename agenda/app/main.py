"""Servicio Agenda: reserva y libera la hora del veterinario (Paso 1 de la Saga)."""
from datetime import datetime

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy.orm import Session

from . import models, schemas
from .database import Base, SessionLocal, engine, get_db

Base.metadata.create_all(bind=engine)  # crea las tablas si no existen

app = FastAPI(title="Agenda")

# Horarios de ejemplo para poder probar la saga sin cargar datos a mano.
# Se insertan solo si la tabla esta vacia: un reinicio del contenedor no debe duplicarlos.
SLOTS_INICIALES = [
    {"veterinario": "Dra. Perez", "inicio": datetime(2026, 11, 23, 9, 0)},
    {"veterinario": "Dra. Perez", "inicio": datetime(2026, 11, 23, 10, 0)},
    {"veterinario": "Dr. Lopez", "inicio": datetime(2026, 11, 23, 11, 0)},
    {"veterinario": "Dr. Lopez", "inicio": datetime(2026, 11, 23, 15, 0)},
]


@app.on_event("startup")
def sembrar_slots():
    """Carga los slots de ejemplo la primera vez que arranca el servicio."""
    db = SessionLocal()
    try:
        if db.query(models.Slot).count() == 0:
            # Todos empiezan LIBRES; el estado lo cambiara reservar() en una transaccion.
            db.add_all([models.Slot(estado="LIBRE", **datos) for datos in SLOTS_INICIALES])
            db.commit()
    finally:
        # La sesion se cierra siempre, incluso si falla la insercion.
        db.close()


@app.get("/health")
def health():
    return {"status": "ok", "service": "agenda"}


@app.get("/slots", response_model=list[schemas.SlotOut])
def listar_slots(db: Session = Depends(get_db)):
    # Ordenamos por id para que la respuesta sea estable y facil de verificar.
    return db.query(models.Slot).order_by(models.Slot.id).all()


@app.get("/slots/{slot_id}", response_model=schemas.SlotOut)
def obtener_slot(slot_id: int, db: Session = Depends(get_db)):
    slot = db.get(models.Slot, slot_id)
    if slot is None:
        raise HTTPException(status_code=404, detail="Slot no encontrado")
    return slot


@app.post("/reservas", response_model=schemas.ReservaOut)
def reservar(datos: schemas.ReservaIn, db: Session = Depends(get_db)):
    """PASO 1 de la Saga: reservar la hora.

    saga_id es la clave de idempotencia: si el orquestador reintenta tras un timeout,
    devolvemos la reserva existente SIN volver a ejecutar la operacion.
    """
    existente = (
        db.query(models.Reserva)
        .filter(models.Reserva.saga_id == datos.saga_id)
        .first()
    )
    if existente is not None:
        return existente

    slot = db.get(models.Slot, datos.slot_id)
    if slot is None:
        raise HTTPException(status_code=404, detail="Slot no encontrado")

    # Actualizacion CONDICIONAL: la BD solo cambia el estado si estaba LIBRE.
    # No basta con leer el estado y luego escribir: dos requests simultaneos podrian
    # leer LIBRE y reservar la misma hora. Una sola sentencia SQL evita esa carrera.
    filas_cambiadas = (
        db.query(models.Slot)
        .filter(models.Slot.id == datos.slot_id, models.Slot.estado == "LIBRE")
        .update({models.Slot.estado: "RESERVADO"}, synchronize_session=False)
    )

    if filas_cambiadas == 0:
        db.rollback()
        # Si otro request con el MISMO saga_id gano la carrera, devolvemos su reserva:
        # para quien reintento, la operacion ya se realizo correctamente.
        ganadora = (
            db.query(models.Reserva)
            .filter(models.Reserva.saga_id == datos.saga_id)
            .first()
        )
        if ganadora is not None:
            return ganadora
        # La hora la tomo otra saga: el cliente debe enterarse con un 409.
        raise HTTPException(status_code=409, detail="La hora ya esta reservada por otra saga")

    reserva = models.Reserva(
        saga_id=datos.saga_id,
        slot_id=datos.slot_id,
        mascota_id=datos.mascota_id,
        estado="ACTIVA",
    )
    db.add(reserva)
    # El UPDATE del slot y el INSERT de la reserva se confirman juntos: si falla
    # algo, no queda una hora marcada como reservada sin su reserva asociada.
    # Ademas, el UNIQUE(saga_id) del modelo impide dos reservas de una misma saga.
    db.commit()
    db.refresh(reserva)
    return reserva


@app.post("/reservas/{saga_id}/liberar", response_model=schemas.ReservaOut)
def liberar(saga_id: str, db: Session = Depends(get_db)):
    """COMPENSACION del Paso 1: liberar la hora.

    Debe ser idempotente y no fallar si la saga nunca llego a reservar: por eso
    no devolvemos 404 cuando no existe la reserva.
    """
    reserva = (
        db.query(models.Reserva)
        .filter(models.Reserva.saga_id == saga_id)
        .first()
    )

    if reserva is None:
        # El orquestador solo necesita un 2xx para seguir con las demas compensaciones.
        # Devolvemos un cuerpo que cumple el contrato sin inventar datos de la BD.
        return schemas.ReservaOut(
            id=0, saga_id=saga_id, slot_id=0, mascota_id=0, estado="LIBERADA"
        )

    if reserva.estado == "LIBERADA":
        # Ya se libero una vez. No volvamos a tocar el slot: otra saga podria
        # haber reservado esa hora despues de la compensacion.
        return reserva

    slot = db.get(models.Slot, reserva.slot_id)
    if slot is not None:
        slot.estado = "LIBRE"
    reserva.estado = "LIBERADA"
    # Los dos cambios (slot y reserva) se guardan en una sola transaccion.
    db.commit()
    db.refresh(reserva)
    return reserva
