"""Observabilidad base para los servicios de la saga.

La logica es comun para agenda, pagos, ficha y orquestador: cada servicio
registra el middleware indicando su nombre.

Aporta:
  1. Un log JSON de UNA SOLA LINEA por request (facil de grep/parsear en Docker).
  2. Contadores en memoria de peticiones y errores por ruta, expuestos en /metrics.

Se usa solo la biblioteca estandar: json, time, logging y threading.
"""
import json
import logging
import re
import threading
import time

from fastapi import Request, Response

# Logger: cada servicio usa su propio nombre (ej. "obs.agenda").
# Le pongo SU PROPIO StreamHandler porque el logger "obs" no es hijo del logger
# que configura uvicorn; sin handler, logging.info() no mostraria nada en Docker.
logger = logging.getLogger("obs")
if not logger.handlers:  # evita duplicar lineas si el modulo se importa dos veces
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(message)s"))  # solo el JSON, sin prefijos
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)

# ---------------------------------------------------------------------------
# Contadores en memoria
# ---------------------------------------------------------------------------
# Un dict simple por ruta es suficiente: solo necesitamos totales, no series
# temporales. El Lock es necesario porque uvicorn atiende requests en DISTINTOS
# hilos; sin el, dos hilos podrian leer y escribir el mismo contador a la vez.
_CONTADORES_LOCK = threading.Lock()
_peticiones_por_ruta: dict[str, int] = {}
_errores_por_ruta: dict[str, int] = {}

# Prefijos de las rutas de la saga que llevan saga_id en la URL. Se enumeran
# explicitamente porque son solo 4 los casos posibles:
#   /reservas/{saga_id}/liberar          (agenda)
#   /pagos/{saga_id}/reembolsar | /pagos/{saga_id}   (pagos)
#   /citas/{saga_id}/anular              (ficha)
#   /citas/{saga_id}                     (orquestador)
_RUTAS_CON_SAGA_ID = ("/reservas/", "/pagos/", "/citas/")
_SUFIJOS_SIN_ID = ("/liberar", "/reembolsar", "/anular")


def _ruta_normalizada(path: str) -> str:
    """Devuelve la ruta sin valores concretos, para agrupar metricas.

    Si no lo hicieramos, /pagos/abc y /pagos/xyz contarian como dos rutas
    distintas y /metrics no serviria para ver cual endpoint esta fallando.
    Solo reemplazamos segmentos que sabemos que son ids; las palabras fijas
    (health, cobrar, confirmar...) se conservan para no mezclar rutas distintas.
    """
    # 1) IDs enteros: /slots/3, /mascotas/1, /resumen-mascota/1.
    ruta = re.sub(r"/(\d+)(?=/|$)", "/{id}", path)

    # 2) saga_id en ruta: solo detras de estos prefijos y NO si es una palabra
    #    fija de la API. Un if explicito es mas claro que un regex generico.
    for prefijo in _RUTAS_CON_SAGA_ID:
        if not ruta.startswith(prefijo):
            continue
        resto = ruta[len(prefijo):]
        primer_segmento = resto.split("/")[0]
        if primer_segmento and primer_segmento != "{id}" and primer_segmento not in ("cobrar", "confirmar"):
            ruta = prefijo + "{id}" + resto[len(primer_segmento):]

    # 3) UUIDs que puedan aparecer en cualquier otra posicion.
    ruta = re.sub(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        "{id}",
        ruta,
    )
    return ruta


def _saga_id_desde_ruta(path: str) -> str | None:
    """Extrae el saga_id cuando va en la URL; devuelve None si no esta."""
    for prefijo in _RUTAS_CON_SAGA_ID:
        if not path.startswith(prefijo):
            continue
        # Lo que sigue al prefijo es el saga_id, quitemos el sufijo de compensacion.
        resto = path[len(prefijo):]
        for sufijo in _SUFIJOS_SIN_ID:
            if resto.endswith(sufijo):
                resto = resto[:-len(sufijo)]
                break
        valor = resto.strip("/").split("/")[0]
        # /reservas (sin id) y /pagos/cobrar no son saga_ids: son rutas fijas.
        if valor and valor not in ("cobrar", "confirmar", "slots", "reservas"):
            return valor
    return None


async def _saga_id_desde_body(request: Request) -> str | None:
    """Extrae saga_id del cuerpo JSON cuando no viene en la ruta.

    Leer el body en un middleware tiene un detalle importante: Starlette lo
    consume. Por eso guardamos los bytes crudos y los devolvemos con
    request._receive (ver abajo); si no, el endpoint recibiria un body vacio.
    Solo intentamos parsear JSON para no penalizar requests sin cuerpo.
    """
    if request.method not in ("POST", "PUT", "PATCH"):
        return None
    cuerpo = await request.body()
    if not cuerpo:
        return None

    # Reinyectamos el body para que el handler pueda leerlo normalmente.
    # Es un atributo interno de Starlette, pero es la forma estandar que usan
    # sus propios middleware.
    async def reenviar():
        return {"type": "http.request", "body": cuerpo, "more_body": False}

    request._receive = reenviar

    try:
        datos = json.loads(cuerpo)
    except (ValueError, UnicodeDecodeError):
        return None  # body no-JSON: simplemente no hay saga_id que extraer.
    if isinstance(datos, dict):
        valor = datos.get("saga_id")
        if isinstance(valor, (str, int)):
            return str(valor)
    return None


def _contar(ruta: str, status: int) -> None:
    """Actualiza ambos contadores bajo un unico lock (operacion atomica)."""
    with _CONTADORES_LOCK:
        _peticiones_por_ruta[ruta] = _peticiones_por_ruta.get(ruta, 0) + 1
        if status >= 400:
            # 4xx y 5xx cuentan como error: son los que degradan la saga.
            _errores_por_ruta[ruta] = _errores_por_ruta.get(ruta, 0) + 1


def instalar_middleware(app, servicio: str) -> None:
    """Registra el middleware de log + metricas en la app FastAPI.

    Se usa una fabrica simple (una funcion que recibe app y servicio) para pasar
    el nombre del servicio sin variables globales nuevas: es la forma que recomienda FastAPI.
    """
    @app.middleware("http")
    async def observar(request: Request, call_next):
        inicio = time.perf_counter()  # reloj monotonic: no salta si cambia la hora del sistema.
        saga_id = _saga_id_desde_ruta(request.url.path)
        if saga_id is None:
            saga_id = await _saga_id_desde_body(request)
        ruta = _ruta_normalizada(request.url.path)
        status = 500  # valor por defecto: si el handler lanza, response no existe.

        try:
            response: Response = await call_next(request)
            status = response.status_code
            return response
        finally:
            # finally se ejecuta SIEMPRE: asi medimos tambien las requests que
            # terminan en excepcion y no se pierde su contador de error.
            duracion_ms = round((time.perf_counter() - inicio) * 1000, 2)
            _contar(ruta, status)

            registro = {
                "servicio": servicio,
                "metodo": request.method,
                "ruta": request.url.path,
                "status": status,
                "duracion_ms": duracion_ms,
                "saga_id": saga_id,  # null cuando la request no pertenece a una saga.
            }
            # json.dumps con separadores compactos => UNA linea, apta para logs de Docker.
            logger.info(json.dumps(registro, separators=(",", ":"), ensure_ascii=False))


def obtener_metricas(servicio: str) -> dict:
    """Devuelve una copia de los contadores para GET /metrics.

    Copiamos bajo lock: si el endpoint serializara el dict original mientras un
    hilo lo está modificando, podriamos obtener un estado a medias.
    """
    with _CONTADORES_LOCK:
        return {
            "servicio": servicio,
            "peticiones_por_ruta": dict(_peticiones_por_ruta),
            "errores_por_ruta": dict(_errores_por_ruta),
            "total_peticiones": sum(_peticiones_por_ruta.values()),
            "total_errores": sum(_errores_por_ruta.values()),
        }
