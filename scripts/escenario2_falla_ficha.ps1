# Escenario 2: falla en el ultimo paso (Ficha) y compensacion en orden inverso.
# Uso (con `docker compose up -d` corriendo):
#   powershell -ExecutionPolicy Bypass -File scripts\escenario2_falla_ficha.ps1
# Es repetible: la falla se activa en caliente y se desactiva al terminar.

$ErrorActionPreference = "Stop"
$raiz = Split-Path -Parent $PSScriptRoot
Set-Location $raiz

$gateway = "http://localhost:8080"
$token = if ($env:GATEWAY_TOKEN) { $env:GATEWAY_TOKEN } else { "token-clinica-123" }
$salida = Join-Path $raiz "evidencia\escenario2\salida.txt"
"" | Set-Content -Path $salida -Encoding utf8

function Log($texto) {
    Write-Host $texto
    Add-Content -Path $salida -Value $texto -Encoding utf8
}

# GET a un servicio interno via docker compose exec (solo comillas simples dentro del codigo Python).
function Get-Interno($servicio, $ruta) {
    $codigo = "import urllib.request; print(urllib.request.urlopen('http://localhost:8000$ruta').read().decode())"
    $texto = docker compose exec -T $servicio python -c $codigo
    return ($texto | ConvertFrom-Json)
}

# Activa o desactiva la falla de Ficha (POST /admin/fallo) sin reiniciar el contenedor.
function Set-FallaFicha([bool]$activa) {
    $valor = if ($activa) { "True" } else { "False" }
    $codigo = "import urllib.request, json; r = urllib.request.Request('http://localhost:8000/admin/fallo', data=json.dumps({'activa': $valor}).encode(), headers={'Content-Type': 'application/json'}); print(urllib.request.urlopen(r).read().decode())"
    docker compose exec -T ficha python -c $codigo | Out-Null
}

$fallos = 0
function Check($ok, $mensaje) {
    if ($ok) { Log "  [OK]    $mensaje" } else { Log "  [FALLA] $mensaje"; $script:fallos++ }
}

Log "=== ESCENARIO 2: falla en Ficha + compensacion ($(Get-Date -Format s)) ==="

$slots = Get-Interno "agenda" "/slots"
$libre = $slots | Where-Object { $_.estado -eq "LIBRE" } | Select-Object -First 1
if (-not $libre) { Log "No hay slots LIBRES. Reinicia con: docker compose down -v"; exit 1 }
$slotId = $libre.id

try {
    Log "`n1) Activando falla en Ficha (POST /admin/fallo)"
    Set-FallaFicha $true

    $clave = "esc2-" + [guid]::NewGuid().ToString()
    $cuerpo = (@{ mascota_id = 1; slot_id = $slotId; monto = 15000 } | ConvertTo-Json)
    $headers = @{ Authorization = "Bearer $token"; "Idempotency-Key" = $clave }
    Log "`n2) POST /api/citas (slot=$slotId, Idempotency-Key=$clave)"
    $saga = Invoke-RestMethod -Method Post -Uri "$gateway/api/citas" -Headers $headers `
        -ContentType "application/json" -Body $cuerpo
    Log ($saga | ConvertTo-Json -Depth 5)

    # 3) Pasos en orden cronologico: confirmar_cita falla y luego se compensa
    #    cobrar_abono y despues reservar_hora (orden inverso).
    Log "`n3) Pasos de la saga (orden cronologico):"
    $saga.pasos | ForEach-Object { Log "   $($_.accion) -> $($_.resultado) $($_.detalle)" }

    # 4) Verificar el estado final en los tres servicios.
    Log "`n4) Verificacion de estado"
    $sagaId = $saga.id
    $slot = Get-Interno "agenda" "/slots/$slotId"
    $pago = Get-Interno "pagos" "/pagos/$sagaId"
    $citas = @(Get-Interno "ficha" "/mascotas/1/citas") | Where-Object { $_.saga_id -eq $sagaId -and $_.estado -eq "CONFIRMADA" }

    $compensados = @($saga.pasos | Where-Object { $_.resultado -eq "COMPENSADO" } | ForEach-Object { $_.accion })
    Check ($saga.estado -eq "COMPENSADA") "Saga COMPENSADA"
    Check (($compensados -join ",") -eq "cobrar_abono,reservar_hora") "Compensacion en orden inverso: $($compensados -join ' -> ')"
    Check ($slot.estado -eq "LIBRE") "Agenda: slot $slotId LIBRE (no queda reserva sin confirmar)"
    Check ($pago.estado -eq "REEMBOLSADO") "Pagos: pago REEMBOLSADO (no queda cobro sin cita)"
    Check (@($citas).Count -eq 0) "Ficha: no hay cita CONFIRMADA para esta saga"
}
finally {
    # Siempre dejamos Ficha sana para no afectar otros escenarios.
    Log "`n5) Desactivando falla en Ficha"
    Set-FallaFicha $false
}

$resultado = if ($fallos -eq 0) { "TODOS LOS CHECKS OK" } else { "$fallos checks fallaron" }
Log "`nResultado: $resultado"
Log "Evidencia guardada en $salida"
exit $fallos
