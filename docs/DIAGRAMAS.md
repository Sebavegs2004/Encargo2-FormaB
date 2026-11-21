# Diagramas de arquitectura (Mermaid)

A continuación se presentan los diagramas de secuencia de los escenarios principales del sistema.

## 1. Flujo feliz — camino exitoso de la Saga

```mermaid
sequenceDiagram
    autonumber
    participant C as Cliente
    participant G as Gateway
    participant O as Orquestador
    participant A as Agenda
    participant P as Pagos
    participant F as Ficha

    C->>G: POST /api/citas (Idempotency-Key)
    G->>O: POST /citas
    O->>A: reservar_hora (saga_id)
    A-->>O: OK (hora reservada)
    O->>P: cobrar_abono (saga_id, monto)
    P-->>O: OK (referencia PAY-xxxx)
    O->>F: confirmar_cita (saga_id)
    F-->>O: OK (cita confirmada)
    O-->>G: Saga COMPLETADA
    G-->>C: 200 OK
```

![Flujo feliz - camino exitoso de la Saga](diagramas/Flujo%20feliz%20(camino%20feliz).png)

## 2. Fallo en Ficha con compensación inversa

```mermaid
sequenceDiagram
    autonumber
    participant C as Cliente
    participant G as Gateway
    participant O as Orquestador
    participant A as Agenda
    participant P as Pagos
    participant F as Ficha

    C->>G: POST /api/citas (Idempotency-Key)
    G->>O: POST /citas
    O->>A: reservar_hora (saga_id)
    A-->>O: OK
    O->>P: cobrar_abono (saga_id, monto)
    P-->>O: OK
    O->>F: confirmar_cita (saga_id)
    F-->>O: 500 (falla inducida /admin/fallo)

    rect rgb(240, 230, 230)
        note right of O: Compensaciones en orden inverso
        O->>F: anular_cita (saga_id)
        F-->>O: OK (idempotente)
        O->>P: reembolsar (saga_id)
        P-->>O: OK (reembolsado)
        O->>A: liberar_hora (saga_id)
        A-->>O: OK (hora liberada)
    end

    O-->>G: Saga COMPENSADA
    G-->>C: 200 OK (estado: COMPENSADA)
```

![Fallo en Ficha con compensación inversa](diagramas/Fallo%20en%20Ficha%20con%20compensaci%C3%B3n%20inversa.png)

## 3. Circuito abierto en Pagos (resiliencia)

```mermaid
sequenceDiagram
    autonumber
    participant O as Orquestador
    participant CB as Circuit Breaker
    participant P as Pagos

    O->>CB: cobrar_abono (saga_id)
    CB->>P: llamada HTTP (timeout 2s)
    P-->>CB: 503 / timeout
    CB-->>O: ERROR (fallo 1)

    O->>CB: cobrar_abono (saga_id)
    CB->>P: llamada HTTP
    P-->>CB: 503 / timeout
    CB-->>O: ERROR (fallo 2)

    O->>CB: cobrar_abono (saga_id)
    CB->>P: llamada HTTP
    P-->>CB: 503 / timeout
    CB-->>O: ERROR (fallo 3 → umbral alcanzado)

    note right of CB: Circuit breaker se ABRE (BREAKER_FAILURE_THRESHOLD=3)

    O->>CB: cobrar_abono (saga_id)
    CB-->>O: Fallback inmediato (pagos_no_disponible) sin llamar a Pagos
    note right of O: Saga termina COMPENSADA rápido, sin colgarse
```

![Circuito abierto en Pagos - resiliencia](diagramas/Circuito%20abierto%20en%20Pagos%20(resiliencia).png)

## 4. Bulkhead (aislamiento de recursos)

El Bulkhead limita las llamadas concurrentes a Pagos mediante un semáforo acotado (`BULKHEAD_MAX_CONCURRENT=5`). Si se excede el cupo, se rechaza inmediatamente para que una dependencia lenta no agote los hilos del Orquestador y el resto del sistema siga funcionando.

---
