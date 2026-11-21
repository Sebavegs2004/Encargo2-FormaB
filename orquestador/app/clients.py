"""Clientes HTTP para llamar a los servicios de dominio desde el orquestador.

Cada función envuelve una llamada a un servicio externo (Agenda, Pagos o Ficha)
usando httpx con el timeout configurado por variable de entorno. Si la respuesta
no es exitosa (status >= 400), se lanza una excepcion para que el orquestador
pueda decidir si compensa o marca la saga como fallida.
"""
import os

import httpx

AGENDA_URL = os.getenv("AGENDA_URL", "http://agenda:8000")
PAGOS_URL = os.getenv("PAGOS_URL", "http://pagos:8000")
FICHA_URL = os.getenv("FICHA_URL", "http://ficha:8000")

TIMEOUT_AGENDA = float(os.getenv("TIMEOUT_AGENDA_SECONDS", "2"))
TIMEOUT_PAGOS = float(os.getenv("TIMEOUT_PAGOS_SECONDS", "2"))
TIMEOUT_FICHA = float(os.getenv("TIMEOUT_FICHA_SECONDS", "2"))


def reservar_hora(saga_id: str, mascota_id: int, slot_id: int):
    """Paso 1 de la Saga: reserva la hora del veterinario en Agenda.

    saga_id se envia como clave de idempotencia para que Agenda no duplique
    la reserva si el orquestador reintenta tras un timeout.
    """
    url = f"{AGENDA_URL}/reservas"
    payload = {"saga_id": saga_id, "mascota_id": mascota_id, "slot_id": slot_id}
    with httpx.Client(timeout=TIMEOUT_AGENDA) as client:
        response = client.post(url, json=payload)
        response.raise_for_status()
        return response.json()


def cobrar_abono(saga_id: str, monto: int):
    """Paso 2 de la Saga: cobra el abono en Pagos.

    Pagos es la dependencia fragil (pasarela externa simulada). El timeout
    y el circuit breaker/bulkhead (commit de Mazulin) se aplican sobre esta
    llamada para evitar cascadas de falla.
    """
    url = f"{PAGOS_URL}/pagos/cobrar"
    payload = {"saga_id": saga_id, "monto": monto}
    with httpx.Client(timeout=TIMEOUT_PAGOS) as client:
        response = client.post(url, json=payload)
        response.raise_for_status()
        return response.json()


def confirmar_cita(saga_id: str, mascota_id: int, slot_id: int):
    """Paso 3 de la Saga: confirma la cita en la ficha de la mascota en Ficha."""
    url = f"{FICHA_URL}/citas/confirmar"
    payload = {"saga_id": saga_id, "mascota_id": mascota_id, "slot_id": slot_id}
    with httpx.Client(timeout=TIMEOUT_FICHA) as client:
        response = client.post(url, json=payload)
        response.raise_for_status()
        return response.json()
