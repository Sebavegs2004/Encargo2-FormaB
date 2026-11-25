"""API Gateway: unico punto de entrada del sistema (puerto 8080).

Solo enruta y autentica: no contiene reglas de negocio ni transforma las
respuestas. Valida el token Bearer y reenvia la peticion al orquestador o al BFF.
"""
import asyncio
import json
import os
import time

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import httpx

from . import ratelimit

app =FastAPI(title="API Gateway")

GATEWAY_TOKEN = os.getenv("GATEWAY_TOKEN", "token-clinica-123")
RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "30"))
BFF_URL = os.getenv("BFF_URL", "http://bff-web:8000")
ORQUESTADOR_URL = os.getenv("ORQUESTADOR_URL", "http://orquestador:8000")
AGENDA_URL = os.getenv("AGENDA_URL", "http://agenda:8000")
PAGOS_URL = os.getenv("PAGOS_URL", "http://pagos:8000")
FICHA_URL = os.getenv("FICHA_URL", "http://ficha:8000")
UPSTREAM_TIMEOUT = float(os.getenv("UPSTREAM_TIMEOUT_SECONDS", "5"))


@app.middleware("http")
async def exigir_token(request: Request, call_next):
    """Exige 'Authorization: Bearer <GATEWAY_TOKEN>' y limita las peticiones por cliente.

    El token se valida solo aqui: los servicios internos no estan expuestos y
    confian en que el gateway ya autentico al cliente. /health queda libre (sin
    token ni limite) para los healthchecks de Docker.
    """
    if request.url.path.startswith("/api/"):
        autorizacion = request.headers.get("Authorization", "")
        if autorizacion != f"Bearer {GATEWAY_TOKEN}":
            return JSONResponse(
                status_code=401,
                content={"detail": "Token invalido o ausente"},
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Rate limiting: se aplica despues de autenticar, por cliente (token + IP).
        ip = request.client.host if request.client else "desconocido"
        espera = ratelimit.revisar(
            f"{autorizacion}|{ip}", ratelimit.limite_actual(RATE_LIMIT_PER_MINUTE)
        )
        if espera > 0:
            return JSONResponse(
                status_code=429,
                content={"detail": "Demasiadas peticiones, intenta mas tarde"},
                headers={"Retry-After": str(espera)},
            )
    return await call_next(request)


async def reenviar(metodo: str, url: str, headers: dict | None = None, body: bytes | None = None) -> Response:
    """Reenvia la peticion al servicio interno y devuelve su status y body tal cual.

    Siempre usa timeout: un servicio colgado no debe colgar tambien al gateway.
    """
    try:
        async with httpx.AsyncClient(timeout=UPSTREAM_TIMEOUT) as client:
            respuesta = await client.request(metodo, url, headers=headers, content=body)
    except httpx.TimeoutException:
        # El servicio interno no respondio a tiempo.
        return JSONResponse(status_code=504, content={"detail": "Timeout esperando al servicio interno"})
    except httpx.RequestError:
        # No se pudo conectar (servicio caido, error de red, etc.).
        return JSONResponse(status_code=502, content={"detail": "No se pudo contactar al servicio interno"})

    return Response(
        content=respuesta.content,
        status_code=respuesta.status_code,
        media_type=respuesta.headers.get("content-type", "application/json"),
    )


@app.get("/health")
def health():
    return {"status": "ok", "service": "api-gateway"}


@app.post("/api/citas")
async def agendar_cita(request: Request):
    """Reenvia la solicitud de agendar al orquestador.

    Propaga el header Idempotency-Key sin tocarlo: la idempotencia la resuelve
    el orquestador, el gateway no guarda estado de negocio.
    """
    headers = {"Content-Type": request.headers.get("Content-Type", "application/json")}
    clave = request.headers.get("Idempotency-Key")
    if clave is not None:
        headers["Idempotency-Key"] = clave
    cuerpo = await request.body()
    return await reenviar("POST", f"{ORQUESTADOR_URL}/citas", headers=headers, body=cuerpo)


@app.get("/api/citas/{saga_id}")
async def consultar_cita(saga_id: str):
    """Consulta el estado de una saga en el orquestador."""
    return await reenviar("GET", f"{ORQUESTADOR_URL}/citas/{saga_id}")


@app.get("/api/bff/resumen-mascota/{mascota_id}")
async def resumen_mascota(mascota_id: int):
    """Pide al BFF el resumen consolidado de una mascota."""
    return await reenviar("GET", f"{BFF_URL}/resumen-mascota/{mascota_id}")


# ---------------------------------------------------------------- administracion
# Estas rutas solo reenvian al endpoint /admin que ya tiene cada servicio. Siguen
# protegidas por el mismo Bearer del gateway y existen para repetir las
# demostraciones en vivo desde el panel web.

async def reenviar_admin(url: str, request: Request) -> Response:
    cuerpo = await request.body()
    return await reenviar(
        "POST",
        url,
        headers={"Content-Type": request.headers.get("Content-Type", "application/json")},
        body=cuerpo,
    )


@app.post("/api/admin/ficha/fallo")
async def admin_fallo_ficha(request: Request):
    """Activa/desactiva la falla inducida al confirmar en Ficha."""
    return await reenviar_admin(f"{FICHA_URL}/admin/fallo", request)


@app.post("/api/admin/pagos/chaos")
async def admin_chaos_pagos(request: Request):
    """Cambia en caliente la latencia y la caida de la pasarela simulada."""
    return await reenviar_admin(f"{PAGOS_URL}/admin/chaos", request)


@app.post("/api/admin/slots")
async def admin_slots(request: Request):
    """Agrega horarios LIBRES para poder repetir la demostracion las veces necesarias."""
    return await reenviar_admin(f"{AGENDA_URL}/admin/slots", request)


@app.get("/api/admin/metricas")
async def admin_metricas():
    """Contadores del orquestador, incluido el estado del breaker y del bulkhead."""
    return await reenviar("GET", f"{ORQUESTADOR_URL}/metrics")


@app.get("/api/admin/salud")
async def admin_salud():
    """Sonda rapida de todos los servicios internos para ver que siguen vivos."""
    objetivos = {
        "bff-web": f"{BFF_URL}/health",
        "orquestador": f"{ORQUESTADOR_URL}/health",
        "agenda": f"{AGENDA_URL}/health",
        "pagos": f"{PAGOS_URL}/health",
        "ficha": f"{FICHA_URL}/health",
    }

    async def sonda(client: httpx.AsyncClient, nombre: str, url: str):
        inicio = time.perf_counter()
        try:
            respuesta = await client.get(url)
            dato = {"ok": respuesta.status_code == 200, "codigo": respuesta.status_code}
        except Exception as exc:
            dato = {"ok": False, "error": type(exc).__name__}
        dato["ms"] = round((time.perf_counter() - inicio) * 1000)
        return nombre, dato

    async with httpx.AsyncClient(timeout=1.5) as client:
        resultados = dict(await asyncio.gather(
            *(sonda(client, nombre, url) for nombre, url in objetivos.items())
        ))
    return {"api-gateway": {"ok": True, "codigo": 200, "ms": 0}, **resultados}


class LimiteIn(BaseModel):
    por_minuto: int = 0  # 0 = volver al valor de entorno


@app.post("/api/admin/limite")
async def admin_limite(datos: LimiteIn):
    """Ajusta el rate limit en caliente para la prueba de carga concurrente."""
    ratelimit.configurar_limite(None if datos.por_minuto <= 0 else datos.por_minuto)
    return {"limite_por_minuto": ratelimit.limite_actual(RATE_LIMIT_PER_MINUTE)}


@app.post("/api/admin/reset")
async def admin_reset():
    """Reinicia TODOS los datos: sagas, pagos, citas, reservas y horarios/mascotas iniciales.

    No reinicia los contenedores: solo llama a /admin/reset de cada servicio en
    orden y deja los indicadores de falla y el rate limit en su estado inicial.
    Debe usarse con el sistema en reposo (sin sagas en curso).
    """
    resultados = {}
    for nombre, url in (
        ("orquestador", f"{ORQUESTADOR_URL}/admin/reset"),
        ("pagos", f"{PAGOS_URL}/admin/reset"),
        ("ficha", f"{FICHA_URL}/admin/reset"),
        ("agenda", f"{AGENDA_URL}/admin/reset"),
    ):
        respuesta = await reenviar("POST", url)
        if respuesta.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"Fallo el reset de {nombre}: {respuesta.body.decode('utf-8', 'replace')}",
            )
        resultados[nombre] = json.loads(respuesta.body)

    cabecera = {"Content-Type": "application/json"}
    await reenviar(
        "POST", f"{FICHA_URL}/admin/fallo",
        headers=cabecera, body=b'{"activa": false}',
    )
    await reenviar(
        "POST", f"{PAGOS_URL}/admin/chaos",
        headers=cabecera, body=b'{"latencia_ms": 0, "falla": false}',
    )
    ratelimit.configurar_limite(None)
    resultados["indicadores"] = {
        "ficha": "sana",
        "pagos": "sano",
        "limite_por_minuto": ratelimit.limite_actual(RATE_LIMIT_PER_MINUTE),
    }
    return resultados


# La web de recepcion se sirve desde el mismo gateway: un solo puerto y un solo
# origen, sin CORS ni un segundo punto de entrada. Se monta al final para que
# /health y /api/* sigan resolviendo primero.
app.mount("/", StaticFiles(directory="web", html=True), name="web")
