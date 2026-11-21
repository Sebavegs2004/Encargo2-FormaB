from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class Saga(Base):
    __tablename__ = "sagas"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_saga_key"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True) 
    idempotency_key: Mapped[str] = mapped_column(String(64))
    mascota_id: Mapped[int] = mapped_column(Integer)
    slot_id: Mapped[int] = mapped_column(Integer)
    monto: Mapped[int] = mapped_column(Integer)
    estado: Mapped[str] = mapped_column(String(20), default="EN_CURSO")
    creada_en: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Lista de pasos/compensaciones ejecutados para esta saga.
    # Se usa en SagaOut (from_attributes) para devolver el historial al cliente.
    pasos: Mapped[list["SagaPaso"]] = relationship(back_populates="saga", cascade="all, delete-orphan")


class SagaPaso(Base):
    """Bitacora de cada paso/compensacion ejecutado (sirve como evidencia ABET 6)."""
    __tablename__ = "saga_pasos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    saga_id: Mapped[str] = mapped_column(ForeignKey("sagas.id"))
    accion: Mapped[str] = mapped_column(String(30))
    resultado: Mapped[str] = mapped_column(String(10))
    detalle: Mapped[str | None] = mapped_column(Text, nullable=True)
    ocurrido_en: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Referencia a la saga a la que pertenece este paso.
    saga: Mapped["Saga"] = relationship(back_populates="pasos")
