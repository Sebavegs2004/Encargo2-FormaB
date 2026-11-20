"""Servicio Pagos: cobra y reembolsa el abono (Paso 2 de la Saga).

Pagos es la DEPENDENCIA FRAGIL del dominio: simula una pasarela externa que puede
ponerse lenta o caerse. Esos valores se cambian en caliente con POST /admin/chaos.
"""
import os
import time
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from . import models, schemas
from .database import Base, engine, get_db

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Pagos")

# Variables de MODULO (no constantes): /admin/chaos las reasigna sin reiniciar el proceso.
# llamar_pasarela_externa las lee en cada llamada, asi que el cambio surte efecto al instante.
PASARELA_LATENCIA_MS = int(os.getenv("PASARELA_LATENCIA_MS", "0"))
PASARELA_FALLA = os.getenv("PASARELA_FALLA", "false").lower() == "true"


class ChaosIn(BaseModel):
    """Cuerpo de /admin/chaos. Se define aqui porque es propio de este servicio."""

    latencia_ms: int = 0  # milisegundos que tarda la pasarela simulada
    falla: bool = False   # True => la pasarela responde 503


@app.get("/health")
def health():
    return {"status": "ok", "service": "pagos"}


def llamar_pasarela_externa(monto: int) -> str:
    """Simula la pasarela externa y devuelve una referencia de pago.

    monto no cambia la simulacion, pero se recibe para respetar la firma real
    de una pasarela de pago.
    """
    # La lentitud es el escenario 3: permite medir timeouts y resiliencia.
    if PASARELA_LATENCIA_MS > 0:
        time.sleep(PASARELA_LATENCIA_MS / 1000)

    if PASARELA_FALLA:
        # 503 = dependencia externa no disponible. Quien llama decide como degradar.
        raise HTTPException(status_code=503, detail="Pasarela externa no disponible")

    # Referencia corta y distinta por cobro: sirve para verificar la evidencia.
    return f"PAY-{uuid4().hex[:8]}"


def buscar_pago(saga_id: str, db: Session) -> models.Pago | None:
    """Centraliza la consulta por saga_id (clave de idempotencia)."""
    return db.query(models.Pago).filter(models.Pago.saga_id == saga_id).first()


@app.post("/pagos/cobrar", response_model=schemas.PagoOut)
def cobrar(datos: schemas.CobroIn, db: Session = Depends(get_db)):
    """PASO 2 de la Saga: cobrar el abono.

    La regla es "un cobro por saga": una vez guardado como COBRADO, cualquier
    reintento devuelve ese mismo pago y NO vuelve a llamar a la pasarela.
    """
    pago = buscar_pago(datos.saga_id, db)

    if pago is not None and pago.estado in ("COBRADO", "REEMBOLSADO"):
        # idempotencia: el reintento tras un timeout no puede cobrar dos veces.
        return pago

    try:
        referencia = llamar_pasarela_externa(datos.monto)
    except HTTPException:
        # La pasarela fallo. Guardamos FALLIDO para dejar rastro del intento.
        # Como no hubo cobro, un reintento posterior puede usar el mismo saga_id.
        if pago is None:
            pago = models.Pago(saga_id=datos.saga_id, monto=datos.monto)
            db.add(pago)
        pago.estado = "FALLIDO"
        pago.referencia_pasarela = None
        db.commit()
        db.refresh(pago)
        # 502: este servicio no pudo completar la operacion con su dependencia.
        raise HTTPException(status_code=502, detail="No se pudo cobrar: pasarela fallida")

    if pago is None:
        pago = models.Pago(saga_id=datos.saga_id, monto=datos.monto)
        db.add(pago)
    else:
        # Estamos reintentando un FALLIDO: se actualiza la MISMA fila.
        # El UNIQUE(saga_id) del modelo impide que existan dos pagos de una saga.
        pago.monto = datos.monto

    pago.estado = "COBRADO"
    pago.referencia_pasarela = referencia
    db.commit()
    db.refresh(pago)
    return pago


@app.post("/pagos/{saga_id}/reembolsar", response_model=schemas.PagoOut)
def reembolsar(saga_id: str, db: Session = Depends(get_db)):
    """COMPENSACION del Paso 2: reembolsar el abono."""
    pago = buscar_pago(saga_id, db)

    if pago is None:
        # La saga pudo fallar antes de cobrar: la compensacion no debe devolver 404.
        return schemas.PagoOut(
            id=0, saga_id=saga_id, monto=0, estado="REEMBOLSADO",
            referencia_pasarela=None,
        )

    if pago.estado == "REEMBOLSADO":
        # Ya se reembolsó: repetir la compensacion no debe reembolsar dos veces.
        return pago

    if pago.estado != "COBRADO":
        # Estado FALLIDO: nunca hubo cobro, por lo tanto no hay nada que devolver.
        return pago

    pago.estado = "REEMBOLSADO"
    db.commit()
    db.refresh(pago)
    return pago


@app.get("/pagos/{saga_id}", response_model=schemas.PagoOut)
def obtener_pago(saga_id: str, db: Session = Depends(get_db)):
    pago = buscar_pago(saga_id, db)
    if pago is None:
        raise HTTPException(status_code=404, detail="Pago no encontrado")
    return pago


@app.post("/admin/chaos")
def configurar_chaos(datos: ChaosIn):
    """Activa/desactiva lentitud y caida en caliente, sin reiniciar el contenedor."""
    # 'global' es necesario porque reasignamos variables que lee la funcion de pasarela.
    global PASARELA_LATENCIA_MS, PASARELA_FALLA
    PASARELA_LATENCIA_MS = max(0, datos.latencia_ms)  # evita latencias negativas
    PASARELA_FALLA = datos.falla
    return {"latencia_ms": PASARELA_LATENCIA_MS, "falla": PASARELA_FALLA}
