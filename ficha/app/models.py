from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class Mascota(Base):
    __tablename__ = "mascotas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nombre: Mapped[str] = mapped_column(String(80))
    especie: Mapped[str] = mapped_column(String(40))
    duenio: Mapped[str] = mapped_column(String(80))


class CitaFicha(Base):
    __tablename__ = "citas_ficha"
    __table_args__ = (UniqueConstraint("saga_id", name="uq_cita_saga"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    saga_id: Mapped[str] = mapped_column(String(64))
    mascota_id: Mapped[int] = mapped_column(ForeignKey("mascotas.id"))
    slot_id: Mapped[int] = mapped_column(Integer)
    # CONFIRMADA | ANULADA
    estado: Mapped[str] = mapped_column(String(20))
    creada_en: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
