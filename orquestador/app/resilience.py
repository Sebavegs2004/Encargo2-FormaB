"""Resiliencia del orquestador frente a Pagos.

Circuit Breaker y respuesta de degradación controlada implementados con la
biblioteca estándar: clases simples, `time` y `threading`.
"""
import json
import logging
import os
import threading
import time

# Umbral de fallos consecutivos que abren el circuito.
BREAKER_FAILURE_THRESHOLD = int(os.getenv("BREAKER_FAILURE_THRESHOLD", "3"))

# Tiempo que el circuito permanece abierto antes de permitir una prueba.
BREAKER_RESET_SECONDS = float(os.getenv("BREAKER_RESET_SECONDS", "10"))

logger = logging.getLogger("resiliencia")
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)

CERRADO = "CERRADO"
ABIERTO = "ABIERTO"
SEMI_ABIERTO = "SEMI_ABIERTO"


class CircuitBreaker:
    """Circuit Breaker con tres estados, protegido por un único Lock."""

    def __init__(self, umbral: int, reinicio_segundos: float, nombre: str = "pagos"):
        self.umbral = umbral
        self.reinicio_segundos = reinicio_segundos
        self.nombre = nombre

        self.estado = CERRADO
        self.fallos_consecutivos = 0

        # monotonic() no salta si cambia la hora del sistema.
        self.abierto_desde: float | None = None

        self.llamadas_rechazadas = 0
        self.llamadas_permitidas = 0
        self._prueba_en_curso = False
        self._lock = threading.Lock()

    def _cambiar_estado(self, nuevo_estado: str, motivo: str) -> None:
        """Registra una transición. Se llama siempre con el lock tomado."""
        anterior = self.estado
        self.estado = nuevo_estado
        if nuevo_estado == ABIERTO:
            self.abierto_desde = time.monotonic()
        else:
            self.abierto_desde = None

        logger.info(json.dumps({
            "evento": "circuit_breaker",
            "dependencia": self.nombre,
            "estado_anterior": anterior,
            "estado_nuevo": nuevo_estado,
            "fallos_consecutivos": self.fallos_consecutivos,
            "motivo": motivo,
        }, separators=(",", ":"), ensure_ascii=False))

    def permitir(self) -> bool:
        """Indica si la llamada puede pasar a la dependencia."""
        with self._lock:
            if self.estado == CERRADO:
                self.llamadas_permitidas += 1
                return True

            if self.estado == ABIERTO:
                tiempo_abierto = time.monotonic() - self.abierto_desde
                if tiempo_abierto < self.reinicio_segundos:
                    # Fail-fast: se evita el timeout y no se carga a Pagos.
                    self.llamadas_rechazadas += 1
                    return False

                self._cambiar_estado(SEMI_ABIERTO, "vencio BREAKER_RESET_SECONDS")
                # Solo una prueba a la vez para no convertirla en avalancha.
                self._prueba_en_curso = True
                self.llamadas_permitidas += 1
                return True

            if self._prueba_en_curso:
                self.llamadas_rechazadas += 1
                return False
            self._prueba_en_curso = True
            self.llamadas_permitidas += 1
            return True

    def registrar_exito(self) -> None:
        """Se llama después de una llamada exitosa a Pagos."""
        with self._lock:
            self.fallos_consecutivos = 0
            if self.estado == SEMI_ABIERTO:
                self._prueba_en_curso = False
                self._cambiar_estado(CERRADO, "prueba semi-abierta exitosa")

    def registrar_fallo(self) -> None:
        """Se llama después de un fallo o timeout al cobrar."""
        with self._lock:
            self.fallos_consecutivos += 1
            if self.estado == SEMI_ABIERTO:
                self._prueba_en_curso = False
                self._cambiar_estado(ABIERTO, "fallo la prueba semi-abierta")
            elif self.estado == CERRADO and self.fallos_consecutivos >= self.umbral:
                self._cambiar_estado(ABIERTO, f"alcanzo {self.umbral} fallos consecutivos")

    def estado_actual(self) -> dict:
        """Snapshot del circuito."""
        with self._lock:
            return {
                "estado": self.estado,
                "fallos_consecutivos": self.fallos_consecutivos,
                "llamadas_permitidas": self.llamadas_permitidas,
                "llamadas_rechazadas": self.llamadas_rechazadas,
            }


# Instancia única compartida por todos los hilos del orquestador.
circuitos = {
    "pagos": CircuitBreaker(
        umbral=BREAKER_FAILURE_THRESHOLD,
        reinicio_segundos=BREAKER_RESET_SECONDS,
        nombre="pagos",
    )
}


class PagosNoDisponible(Exception):
    """El circuito rechazó la llamada a Pagos."""


def fallback_pagos() -> dict:
    """Respuesta controlada cuando no se puede cobrar."""
    return {
        "estado": "pagos_no_disponible",
        "degradado": True,
        "mensaje": "Pagos no esta disponible; la saga se compensara sin llamar a la pasarela",
    }
