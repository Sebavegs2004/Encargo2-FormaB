# Clinica veterinaria - Agendar una consulta

Sistema de microservicios para agendar una consulta veterinaria. Agendar implica tres pasos que viven en
servicios distintos, y se coordinan con una **Saga orquestada**:

1. **Agenda** reserva la hora.
2. **Pagos** cobra el abono.
3. **Ficha** confirma la cita.

Si un paso falla, el orquestador deshace los anteriores en orden inverso (anular cita, reembolsar, liberar hora).

## Servicios

| Servicio      | Que hace                                              | Puerto publicado |
|---------------|-------------------------------------------------------|------------------|
| `api-gateway` | Unico punto de entrada: token Bearer y rate limiting  | 8080             |
| `bff-web`     | Resumen de una mascota (Ficha + Agenda)               | -                |
| `orquestador` | Saga, compensaciones, Circuit Breaker y Bulkhead      | -                |
| `agenda`      | Horas disponibles y reservas                          | -                |
| `pagos`       | Cobros y reembolsos (pasarela de pago simulada)       | -                |
| `ficha`       | Mascotas y citas                                      | -                |

Cada servicio tiene su propia base de datos. Las decisiones de diseno estan en
[docs/DECISIONES.md](docs/DECISIONES.md).

## Requisitos

- Docker Desktop (con `docker compose`)
- PowerShell (para los scripts de los escenarios)

## Como levantarlo

```powershell
docker compose up --build -d
docker compose ps
curl.exe http://localhost:8080/health
```

Los 6 servicios deben quedar `healthy` y solo el puerto 8080 aparece publicado. Para apagarlo y borrar los datos:

```powershell
docker compose down -v
```

Configuracion opcional (variables de entorno o archivo `.env`, ver `.env.example`):

| Variable                | Por defecto         | Para que sirve                              |
|-------------------------|---------------------|---------------------------------------------|
| `GATEWAY_TOKEN`         | `token-clinica-123` | Token Bearer que exige el gateway           |
| `RATE_LIMIT_PER_MINUTE` | `30`                | Peticiones por minuto por cliente           |
| `BREAKER_ENABLED`       | `true`              | Activa el Circuit Breaker sobre Pagos       |
| `BULKHEAD_ENABLED`      | `true`              | Activa el Bulkhead sobre Pagos              |

## Usar la API

Todas las rutas `/api/*` requieren el header `Authorization: Bearer token-clinica-123`.

```powershell
# Resumen de una mascota
curl.exe -H "Authorization: Bearer token-clinica-123" http://localhost:8080/api/bff/resumen-mascota/1

# Agendar una consulta (la Idempotency-Key evita agendar dos veces la misma solicitud)
curl.exe -X POST http://localhost:8080/api/citas -H "Authorization: Bearer token-clinica-123" `
  -H "Idempotency-Key: demo-1" -H "Content-Type: application/json" `
  -d '{\"mascota_id\":1,\"slot_id\":1,\"monto\":15000}'

# Ver el estado de una saga
curl.exe -H "Authorization: Bearer token-clinica-123" http://localhost:8080/api/citas/<saga_id>
```

El gateway responde `401` sin token valido, `429` (con `Retry-After`) si se pasa del limite,
`504` si un servicio interno tarda demasiado y `502` si no se puede conectar.

## Web de recepcion

El gateway sirve tambien la interfaz web en [http://localhost:8080](http://localhost:8080) (sin puertos nuevos):

- **Agendar consulta**: elige mascota, horario y abono; muestra el resultado de la saga y su bitacora.
- **Mascotas y citas**: ficha, citas y disponibilidad, con avisos si el BFF no puede componer una seccion.
- **Estado de solicitud**: busca una saga por ID para revisar sus pasos y compensaciones.
- **Administración**: induce la falla de Ficha y la lentitud/caída de Pagos, consulta salud y metricas
  del orquestador, agrega horarios y ajusta el rate limit para repetir las demostraciones.

La web usa el mismo token del gateway (`api-gateway/web/config.js`) y reintenta con la misma
`Idempotency-Key` si la peticion se pierde. Levanta el stack con `docker compose up --build -d` y
abrila directamente en [http://localhost:8080](http://localhost:8080).

## Escenarios de prueba

Los tres scripts se ejecutan desde la raiz del repositorio, con el sistema levantado, y guardan su salida
en la carpeta `evidencia/`. Para repetir un escenario desde cero:

```powershell
docker compose down -v
docker compose up --build -d
```

### Escenario 1: exito e idempotencia

```powershell
powershell -ExecutionPolicy Bypass -File scripts\escenario1_exito.ps1
```

Agenda una consulta por el gateway y repite la misma peticion con la misma `Idempotency-Key`.
Salida en `evidencia/escenario1/salida.txt`.

- [x] La saga termina `COMPLETADA` con los 3 pasos en OK
- [x] Repetir la misma `Idempotency-Key` devuelve la misma saga y no agrega pasos
- [x] Agenda: la hora queda `RESERVADO`
- [x] Pagos: el pago queda `COBRADO` (un solo cobro)
- [x] Ficha: hay exactamente una cita `CONFIRMADA`

### Escenario 2: falla en Ficha y compensacion

```powershell
powershell -ExecutionPolicy Bypass -File scripts\escenario2_falla_ficha.ps1
```

Activa la falla de Ficha (`POST /admin/fallo`), agenda una consulta y la desactiva al terminar.
Salida en `evidencia/escenario2/salida.txt`.

- [x] La saga termina `COMPENSADA`
- [x] Las compensaciones van en orden inverso: primero `cobrar_abono`, despues `reservar_hora`
- [x] Agenda: la hora vuelve a `LIBRE` (no queda reserva sin confirmar)
- [x] Pagos: el pago queda `REEMBOLSADO` (no queda cobro sin cita)
- [x] Ficha: no hay ninguna cita `CONFIRMADA` para esa saga

### Escenario 3: Pagos lento y luego caido (antes y despues)

```powershell
powershell -ExecutionPolicy Bypass -File scripts\escenario3_pagos_lento.ps1
```

Tarda unos minutos porque repite el mismo experimento dos veces, recreando el orquestador y el gateway:

- **antes:** sin patrones (`BREAKER_ENABLED=false`, `BULKHEAD_ENABLED=false`)
- **despues:** con patrones (`BREAKER_ENABLED=true`, `BULKHEAD_ENABLED=true`)

En cada corrida mide un baseline con Pagos sano, pone Pagos lento (3 s, mas que el timeout de 2 s) y lanza
12 peticiones concurrentes, luego pone Pagos caido y lanza 6 peticiones seguidas. Despues lee `/metrics` del
orquestador y comprueba que el gateway y el BFF siguen respondiendo. Salida en
`evidencia/escenario3/antes/`, `evidencia/escenario3/despues/` y `evidencia/escenario3/comparacion.txt`.

Resultado de una corrida:

| Metrica                           | antes   | despues |
|-----------------------------------|---------|---------|
| Pagos lento: tiempo promedio      | 2499 ms | 1001 ms |
| Estado final del circuito         | CERRADO | ABIERTO |
| Rechazos por circuito abierto     | 0       | 6       |
| Rechazos por bulkhead             | 0       | 7       |
| Gateway y BFF siguen respondiendo | si      | si      |

- [x] Antes: cada cobro lento espera el timeout completo
- [x] Despues: el bulkhead rechaza de inmediato lo que supera el cupo de 5 llamadas concurrentes
- [x] Despues: el circuito se abre y responde rapido, sin llamar a Pagos
- [x] En ambos casos las sagas terminan `COMPENSADA` y el gateway y el BFF siguen respondiendo

Mientras corre, el script sube `RATE_LIMIT_PER_MINUTE` a 1000 para que el limite del gateway no interfiera.

## Consultar un servicio interno

Los servicios internos no publican puertos. Para consultar uno de ellos:

```powershell
docker compose exec -T agenda python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/slots').read().decode())"
```

Tambien se puede crear un `docker-compose.override.yml` local (esta en `.gitignore`) que publique sus puertos.
