"""Esquemas de la API del Orquestador."""
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AgendarIn(BaseModel):
    mascota_id: int
    slot_id: int
    monto: int


class PasoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    accion: str
    resultado: str
    detalle: str | None = None
    ocurrido_en: datetime


class SagaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    mascota_id: int
    slot_id: int
    monto: int
    estado: str
    pasos: list[PasoOut] = []
