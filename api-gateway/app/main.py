"""API Gateway: unico punto de entrada del sistema (puerto 8080).

Solo enruta y autentica: no contiene reglas de negocio ni transforma las
respuestas. Valida el token Bearer y reenvia la peticion al orquestador o al BFF.
"""
import os

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
import httpx

from . import ratelimit

app =FastAPI(title="API Gateway")

GATEWAY_TOKEN = os.getenv("GATEWAY_TOKEN", "token-clinica-123")
RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "30"))
BFF_URL = os.getenv("BFF_URL", "http://bff-web:8000")
ORQUESTADOR_URL = os.getenv("ORQUESTADOR_URL", "http://orquestador:8000")
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
        espera = ratelimit.revisar(f"{autorizacion}|{ip}", RATE_LIMIT_PER_MINUTE)
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
