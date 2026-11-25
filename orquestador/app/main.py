import os
import uuid

from fastapi import Depends, FastAPI, Header, HTTPException
from sqlalchemy.orm import Session

from . import models, schemas
from .clients import (
    anular_cita,
    cobrar_abono,
    confirmar_cita,
    liberar_hora,
    reservar_hora,
    reembolsar,
)
from .database import Base, engine, get_db
from .observabilidad import instalar_middleware, obtener_metricas
from .resilience import metricas_resiliencia

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Orquestador")

instalar_middleware(app, servicio="orquestador")


@app.get("/metrics")
def metrics():
    """Contadores de requests y estado de los patrones de resiliencia."""
    datos = obtener_metricas("orquestador")
    datos["resiliencia"] = metricas_resiliencia()
    return datos

AGENDA_URL = os.getenv("AGENDA_URL", "http://agenda:8000")
PAGOS_URL = os.getenv("PAGOS_URL", "http://pagos:8000")
FICHA_URL = os.getenv("FICHA_URL", "http://ficha:8000")

TIMEOUT_AGENDA = float(os.getenv("TIMEOUT_AGENDA_SECONDS", "2"))
TIMEOUT_PAGOS = float(os.getenv("TIMEOUT_PAGOS_SECONDS", "2"))
TIMEOUT_FICHA = float(os.getenv("TIMEOUT_FICHA_SECONDS", "2"))


@app.get("/health")
def health():
    return {"status": "ok", "service": "orquestador"}


def registrar_paso(db: Session, saga_id: str, accion: str, resultado: str, detalle: str | None = None):
    """Escribe un registro en la bitacora de pasos de la saga (SagaPaso).

    Sirve como evidencia de trazas (ABET 6): queda constancia de que paso
    se ejecuto, con que resultado y cuando ocurrio.
    """
    paso = models.SagaPaso(
        saga_id=saga_id,
        accion=accion,
        resultado=resultado,
        detalle=detalle,
    )
    db.add(paso)
    db.commit()
    return paso


@app.post("/citas", response_model=schemas.SagaOut)
def agendar_consulta(
    datos: schemas.AgendarIn,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    db: Session = Depends(get_db),
):
    """Ejecuta la Saga de agendar consulta: Agenda -> Pagos -> Ficha.

    Idempotencia: si ya existe una saga con el mismo Idempotency-Key, se
    devuelve la misma saga sin ejecutar nada de nuevo (evita doble reserva,
    doble cobro o doble confirmacion).
    """
    # Idempotencia: clave repetida => devolver la saga ya creada.
    existente = (
        db.query(models.Saga)
        .filter(models.Saga.idempotency_key == idempotency_key)
        .first()
    )
    if existente is not None:
        return existente

    # Crear la saga en estado EN_CURSO.
    saga_id = str(uuid.uuid4())
    saga = models.Saga(
        id=saga_id,
        idempotency_key=idempotency_key,
        mascota_id=datos.mascota_id,
        slot_id=datos.slot_id,
        monto=datos.monto,
        estado="EN_CURSO",
    )
    db.add(saga)
    db.commit()
    db.refresh(saga)

    # Lista de pasos ya ejecutados con exito: (nombre_paso, funcion_compensacion).
    # Se usa para compensar en orden inverso si un paso posterior falla.
    pasos_completados = []

    # Mapeo de cada paso hacia su funcion de compensacion.
    compensaciones = {
        "reservar_hora": liberar_hora,
        "cobrar_abono": reembolsar,
        "confirmar_cita": anular_cita,
    }

    try:
        # Paso 1: reservar la hora en Agenda.
        paso_actual = "reservar_hora"
        reservar_hora(saga_id, datos.mascota_id, datos.slot_id)
        registrar_paso(db, saga_id, paso_actual, "OK")
        pasos_completados.append(paso_actual)

        # Paso 2: cobrar el abono en Pagos.
        paso_actual = "cobrar_abono"
        cobrar_abono(saga_id, datos.monto)
        registrar_paso(db, saga_id, paso_actual, "OK")
        pasos_completados.append(paso_actual)

        # Paso 3: confirmar la cita en Ficha.
        paso_actual = "confirmar_cita"
        confirmar_cita(saga_id, datos.mascota_id, datos.slot_id)
        registrar_paso(db, saga_id, paso_actual, "OK")
        pasos_completados.append(paso_actual)

        # Todos los pasos fueron exitosos: marcar la saga como COMPLETADA.
        saga.estado = "COMPLETADA"
        db.commit()
        db.refresh(saga)
        return saga

    except Exception as exc:
        # Un paso fallo. Registramos el paso fallido como ERROR.
        detalle = f"{type(exc).__name__}: {exc}"
        if paso_actual is not None:
            registrar_paso(db, saga_id, paso_actual, "ERROR", detalle)

        # Compensar en orden inverso: del ultimo paso OK al primero.
        estado_final = "COMPENSADA"
        for paso in reversed(pasos_completados):
            funcion_compensacion = compensaciones[paso]
            try:
                funcion_compensacion(saga_id)
                registrar_paso(db, saga_id, paso, "COMPENSADO")
            except Exception as exc_comp:
                # Si una compensacion falla, no podemos garantizar consistencia.
                detalle_comp = f"{type(exc_comp).__name__}: {exc_comp}"
                registrar_paso(
                    db, saga_id, paso, "ERROR", f"compensacion fallo: {detalle_comp}"
                )
                estado_final = "FALLIDA"

        saga.estado = estado_final
        db.commit()
        db.refresh(saga)
        return saga


@app.get("/citas/{saga_id}", response_model=schemas.SagaOut)
def consultar_saga(saga_id: str, db: Session = Depends(get_db)):
    """Devuelve el estado de una saga y el historial de sus pasos."""
    saga = db.get(models.Saga, saga_id)
    if saga is None:
        raise HTTPException(status_code=404, detail="Saga no encontrada")
    return saga


@app.post("/admin/reset")
def reset_datos_admin(db: Session = Depends(get_db)):
    """Borra sagas y su bitacora para empezar una demostracion desde cero."""
    pasos = db.query(models.SagaPaso).count()
    sagas = db.query(models.Saga).count()
    db.query(models.SagaPaso).delete()
    db.query(models.Saga).delete()
    db.commit()
    return {"sagas_eliminadas": sagas, "pasos_eliminados": pasos}


