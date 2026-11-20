from pydantic import BaseModel, ConfigDict


class MascotaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    nombre: str
    especie: str
    duenio: str


class ConfirmarIn(BaseModel):
    saga_id: str       # clave de idempotencia
    mascota_id: int
    slot_id: int


class CitaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    saga_id: str
    mascota_id: int
    slot_id: int
    estado: str
