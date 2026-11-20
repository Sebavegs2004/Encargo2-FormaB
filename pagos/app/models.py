from datetime import datetime

from sqlalchemy import DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class Pago(Base):
    __tablename__ = "pagos"
    __table_args__ = (UniqueConstraint("saga_id", name="uq_pago_saga"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    saga_id: Mapped[str] = mapped_column(String(64))  # clave de idempotencia
    monto: Mapped[int] = mapped_column(Integer)       # CLP, sin decimales
    # COBRADO | REEMBOLSADO | FALLIDO
    estado: Mapped[str] = mapped_column(String(20))
    referencia_pasarela: Mapped[str | None] = mapped_column(String(64), nullable=True)
    creado_en: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
