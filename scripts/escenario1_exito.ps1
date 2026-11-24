# Escenario 1: saga exitosa + idempotencia.
# Uso (con `docker compose up -d` corriendo):
#   powershell -ExecutionPolicy Bypass -File scripts\escenario1_exito.ps1
# Para repetirlo desde cero: docker compose down -v ; docker compose up --build -d
# (el escenario toma el primer slot LIBRE, asi que cada corrida limpia usa el slot 1).

$ErrorActionPreference = "Stop"
$raiz = Split-Path -Parent $PSScriptRoot
Set-Location $raiz

$gateway = "http://localhost:8080"
$token = if ($env:GATEWAY_TOKEN) { $env:GATEWAY_TOKEN } else { "token-clinica-123" }
$salida = Join-Path $raiz "evidencia\escenario1\salida.txt"
"" | Set-Content -Path $salida -Encoding utf8

# Escribe en pantalla y en el archivo de evidencia.
function Log($texto) {
    Write-Host $texto
    Add-Content -Path $salida -Value $texto -Encoding utf8
}

# GET a un servicio interno (no tienen puerto publicado) via docker compose exec.
# Dentro del codigo Python usamos solo comillas simples para que PowerShell no lo rompa.
function Get-Interno($servicio, $ruta) {
    $codigo = "import urllib.request; print(urllib.request.urlopen('http://localhost:8000$ruta').read().decode())"
    $texto = docker compose exec -T $servicio python -c $codigo
    return ($texto | ConvertFrom-Json)
}

# POST de la saga por el gateway, con token e Idempotency-Key.
function Post-Cita($clave, $cuerpo) {
    $headers = @{ Authorization = "Bearer $token"; "Idempotency-Key" = $clave }
    return Invoke-RestMethod -Method Post -Uri "$gateway/api/citas" -Headers $headers `
        -ContentType "application/json" -Body $cuerpo
}

$fallos = 0
function Check($ok, $mensaje) {
    if ($ok) { Log "  [OK]    $mensaje" } else { Log "  [FALLA] $mensaje"; $script:fallos++ }
}

Log "=== ESCENARIO 1: exito + idempotencia ($(Get-Date -Format s)) ==="

# 1) Elegir un slot LIBRE consultando Agenda.
Log "`n1) Slots en Agenda:"
$slots = Get-Interno "agenda" "/slots"
$slots | ForEach-Object { Log "   slot $($_.id) $($_.veterinario) $($_.inicio) -> $($_.estado)" }
$libre = $slots | Where-Object { $_.estado -eq "LIBRE" } | Select-Object -First 1
if (-not $libre) { Log "No hay slots LIBRES. Reinicia con: docker compose down -v"; exit 1 }
$slotId = $libre.id

# 2) Ejecutar la saga por el gateway.
$clave = "esc1-" + [guid]::NewGuid().ToString()
$cuerpo = (@{ mascota_id = 1; slot_id = $slotId; monto = 15000 } | ConvertTo-Json)
Log "`n2) POST /api/citas (Idempotency-Key=$clave, slot=$slotId)"
$saga1 = Post-Cita $clave $cuerpo
Log ($saga1 | ConvertTo-Json -Depth 5)

# 3) Repetir la MISMA peticion: debe devolver la misma saga sin ejecutar nada de nuevo.
Log "`n3) Repeticion con la misma Idempotency-Key"
$saga2 = Post-Cita $clave $cuerpo
Log "   saga original: $($saga1.id) | saga repetida: $($saga2.id)"

# 4) Verificar el estado final en los tres servicios.
Log "`n4) Verificacion de estado"
$sagaId = $saga1.id
$slot = Get-Interno "agenda" "/slots/$slotId"
$pago = Get-Interno "pagos" "/pagos/$sagaId"
$citas = @(Get-Interno "ficha" "/mascotas/1/citas") | Where-Object { $_.saga_id -eq $sagaId }

Check ($saga1.estado -eq "COMPLETADA") "Saga COMPLETADA"
Check ($saga2.id -eq $saga1.id) "Idempotencia: misma saga al repetir la clave"
Check (@($saga2.pasos).Count -eq 3) "La repeticion no agrego pasos (siguen siendo 3)"
Check ($slot.estado -eq "RESERVADO") "Agenda: slot $slotId RESERVADO"
Check ($pago.estado -eq "COBRADO") "Pagos: pago COBRADO (ref $($pago.referencia_pasarela))"
Check (@($citas).Count -eq 1 -and $citas[0].estado -eq "CONFIRMADA") "Ficha: exactamente 1 cita CONFIRMADA"

$resultado = if ($fallos -eq 0) { "TODOS LOS CHECKS OK" } else { "$fallos checks fallaron" }
Log "`nResultado: $resultado"
Log "Evidencia guardada en $salida"
exit $fallos
