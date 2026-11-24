"""Rate limiting por cliente para el API Gateway.

Ventana fija de 60 segundos guardada en memoria. Se eligio ventana fija por
simpleza: por cada cliente solo se guarda el inicio de la ventana y un contador.

Limitacion: el estado vive en el proceso del gateway, asi que se pierde al
reiniciar y no se comparte si hubiera varias replicas (haria falta Redis).
"""
import threading
import time

VENTANA_SEGUNDOS = 60

# El Lock evita que dos peticiones simultaneas lean y escriban el contador a la vez.
_lock = threading.Lock()

# cliente -> [inicio_de_ventana, cantidad_de_peticiones]
_contadores: dict[str, list] = {}


def revisar(cliente: str, limite: int) -> int:
    """Registra una peticion del cliente y decide si puede pasar.

    Devuelve 0 si la peticion puede pasar, o los segundos que debe esperar
    (valor para el header Retry-After) si ya excedio el limite de la ventana.
    """
    # monotonic() no salta si cambia la hora del sistema.
    ahora = time.monotonic()
    with _lock:
        registro = _contadores.get(cliente)

        if registro is None or ahora - registro[0] >= VENTANA_SEGUNDOS:
            # Ventana nueva: se reinicia el contador con esta peticion.
            _contadores[cliente] = [ahora, 1]
            return 0

        if registro[1] >= limite:
            restante = VENTANA_SEGUNDOS - (ahora - registro[0])
            return max(1, int(restante) + 1)

        registro[1] += 1
        return 0
