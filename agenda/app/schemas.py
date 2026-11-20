from datetime import datetime

from pydantic import BaseModel, ConfigDict


class SlotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    veterinario: str
    inicio: datetime
    estado: str


class ReservaIn(BaseModel):
    saga_id: str    
    slot_id: int
    mascota_id: int


class ReservaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    saga_id: str
    slot_id: int
    mascota_id: int
    estado: str
