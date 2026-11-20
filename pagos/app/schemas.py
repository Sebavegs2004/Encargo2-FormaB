from pydantic import BaseModel, ConfigDict


class CobroIn(BaseModel):
    saga_id: str   # clave de idempotencia
    monto: int


class PagoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    saga_id: str
    monto: int
    estado: str
    referencia_pasarela: str | None = None
