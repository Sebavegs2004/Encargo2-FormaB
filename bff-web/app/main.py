"""BFF Web: compone en UNA respuesta los datos que necesita la pantalla web.

Regla (ABET 2): el BFF solo COMPONE para su cliente; no contiene reglas de negocio
ni escribe datos. Agrega datos de Ficha y Agenda en una sola respuesta.
"""
import os

from fastapi import FastAPI, HTTPException
import httpx

app = FastAPI(title="BFF Web")

AGENDA_URL = os.getenv("AGENDA_URL", "http://agenda:8000")
PAGOS_URL = os.getenv("PAGOS_URL", "http://pagos:8000")
FICHA_URL = os.getenv("FICHA_URL", "http://ficha:8000")
TIMEOUT = float(os.getenv("UPSTREAM_TIMEOUT_SECONDS", "2"))


@app.get("/health")
def health():
    return {"status": "ok", "service": "bff-web"}


def _fetch(client: httpx.Client, url: str):
    """Realiza una peticion HTTP con timeout y devuelve el JSON o None si falla.

    Se usa para llamar a los servicios de dominio sin que una caida de uno de
    ellos bloquee la respuesta completa del BFF (degradacion elegante).
    """
    try:
        response = client.get(url, timeout=TIMEOUT)
        response.raise_for_status()
        return response.json()
    except Exception:
        return None


@app.get("/resumen-mascota/{mascota_id}")
def resumen_mascota(mascota_id: int):
    """Vista consolidada para la pantalla 'Mi mascota'.

    Agrega datos de Ficha (mascota + citas) y Agenda (slots disponibles) en una
    sola respuesta. Si un servicio no responde, se devuelve igual la parte
    disponible y se indica en "advertencias" cuales secciones faltan.

    El BFF solo compone: no aplica reglas de negocio ni escribe datos.
    """
    advertencias = []
    mascota = None
    citas = []
    slots = []

    with httpx.Client() as client:
        # --- Ficha: datos de la mascota ---
        mascota_data = _fetch(
            client, f"{FICHA_URL}/mascotas/{mascota_id}"
        )
        if mascota_data is not None:
            mascota = mascota_data
        else:
            advertencias.append("Ficha no disponible: no se pudo obtener la mascota")

        # --- Ficha: citas de la mascota ---
        citas_data = _fetch(
            client, f"{FICHA_URL}/mascotas/{mascota_id}/citas"
        )
        if citas_data is not None:
            citas = citas_data
        elif mascota is not None:
            # Solo advertimos si al menos la mascota existio; si Ficha esta
            # completamente caida ya se advirtio arriba.
            advertencias.append("Ficha no disponible: no se pudieron obtener las citas")

        # --- Agenda: slots disponibles ---
        slots_data = _fetch(client, f"{AGENDA_URL}/slots")
        if slots_data is not None:
            slots = slots_data
        else:
            advertencias.append("Agenda no disponible: no se pudieron obtener los slots")

    return {
        "mascota_id": mascota_id,
        "mascota": mascota,
        "citas": citas,
        "slots": slots,
        "advertencias": advertencias,
    }
