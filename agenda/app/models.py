from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class Slot(Base):
    __tablename__ = "slots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    veterinario: Mapped[str] = mapped_column(String(80))
    inicio: Mapped[datetime] = mapped_column(DateTime)
    # LIBRE | RESERVADO
    estado: Mapped[str] = mapped_column(String(20), default="LIBRE")


class Reserva(Base):
    __tablename__ = "reservas"
    __table_args__ = (UniqueConstraint("saga_id", name="uq_reserva_saga"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    saga_id: Mapped[str] = mapped_column(String(64))  # clave de idempotencia
    slot_id: Mapped[int] = mapped_column(ForeignKey("slots.id"))
    mascota_id: Mapped[int] = mapped_column(Integer)
    # ACTIVA | LIBERADA (compensacion)
    estado: Mapped[str] = mapped_column(String(20), default="ACTIVA")
    creada_en: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
