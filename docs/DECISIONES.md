# Decisiones de arquitectura

Este documento justifica las dos decisiones de diseño principales del encargo:
(a) por qué se usó Orquestación y no Coreografía para la Saga, y
(b) por qué se usó Saga y no Two-Phase Commit (2PC) para este caso.

---

## (a) Orquestación vs Coreografía

### Orquestación (elegida)

Existe un coordinador central (el servicio **Orquestador**) que conoce el estado de la saga (`EN_CURSO`, `COMPLETADA`, `FALLIDA`, `COMPENSADA`) y decide el orden exacto de ejecución:

1. Paso 1: Agenda (reservar hora)
2. Paso 2: Pagos (cobrar abono)
3. Paso 3: Ficha (confirmar cita)

Si un paso falla, el orquestador sabe exactamente qué pasos ya se confirmaron y ejecuta las compensaciones **en orden inverso** de forma determinista (Ficha.anular → Pagos.reembolsar → Agenda.liberar).

**Ventajas para este encargo:**

- **Trazabilidad clara:** cada paso queda registrado en la tabla `saga_pasos` (evidencia para ABET 6).
- **Fácil de explicar en defensa oral:** hay un único lugar que coordina la transacción distribuida, por lo que cualquier línea de código puede explicarse de forma directa.
- **Control del orden de compensación:** el orquestador garantiza que las compensaciones se ejecuten al revés de los pasos confirmados.

**Desventaja:** el orquestador es un punto único de fallo. Sin embargo, es aceptable porque es un componente simple, sin lógica de negocio compleja, y su única responsabilidad es coordinar la saga.

### Coreografía (descartada)

En la coreografía no hay coordinador; cada servicio emite eventos y los demás reaccionan.

**Desventajas:**

- Es difícil rastrear el estado global de la saga.
- Es difícil garantizar que las compensaciones se ejecuten en el orden correcto.
- Es más complejo de evidenciar para ABET 6, porque no hay un registro central de qué paso se ejecutó y con qué resultado.

### Conclusión

Se eligió **orquestación** porque el encargo exige demostrar el camino feliz y la compensación en orden inverso de forma clara, reproducible y evidenciable. La orquestación ofrece un punto único de control que facilita ambas cosas.

---

## (b) Saga vs Two-Phase Commit (2PC)

### Por qué Saga y no 2PC

1. **La pasarela externa no participa en 2PC:** El servicio Pagos simula una pasarela de pago externa. Una pasarela externa no soporta el protocolo 2PC (no puede responder "preparado" y luego confirmar o deshacer de forma coordinada con nosotros). Saga no requiere que los participantes soporten 2PC; solo invoca sus APIs normales y compensa si algo falla.

2. **Bloqueos largos afectan la disponibilidad:** En 2PC, los recursos se mantienen bloqueados hasta que todos los participantes votan y confirman. En nuestro caso, la hora del veterinario quedaría bloqueada mientras esperamos la pasarela de pagos y la ficha, reduciendo la disponibilidad del sistema.

3. **Disponibilidad y degradación elegante:** Saga permite que cada servicio avance de forma autónoma y que, ante una falla, se compense solo lo necesario. Además, si Pagos cae, el Circuit Breaker permite fallar rápido sin bloquear toda la transacción distribuida.

### Conclusión

Saga es el patrón adecuado para este dominio porque trabajamos con servicios autónomos, una dependencia externa (pasarela de pagos) que no soporta 2PC, y necesitamos mantener la disponibilidad del sistema incluso cuando algo falla.

---

## Imágenes de referencia para la defensa

Cuando dispongas de las capturas de pantalla, inserta aquí las imágenes que respaldan cada decisión:

```markdown
![Diagrama de orquestación](docs/imagenes/orquestacion_vs_coreografia.png)
![Justificación Saga vs 2PC](docs/imagenes/saga_vs_2pc.png)
```

Guarda las imágenes en `docs/imagenes/` y ajusta las rutas según corresponda.
