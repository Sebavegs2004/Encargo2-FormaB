# Escenario 3: Pagos lento y luego caido, SIN y CON patrones de resiliencia.
#
# Corre dos veces el mismo experimento:
#   "antes"   -> BREAKER_ENABLED=false y BULKHEAD_ENABLED=false (sin patrones)
#   "despues" -> BREAKER_ENABLED=true  y BULKHEAD_ENABLED=true  (con patrones)
#
# Uso (con el stack levantado: docker compose up --build -d):
#   powershell -ExecutionPolicy Bypass -File scripts\escenario3_pagos_lento.ps1
#
# Cada corrida:
#   1) recrea orquestador y gateway con las variables del modo (asi /metrics parte en cero),
#   2) toma un baseline con Pagos sano,
#   3) pone Pagos LENTO (POST /admin/chaos) y lanza muchas peticiones concurrentes por el gateway,
#   4) pone Pagos CAIDO y lanza peticiones seguidas,
#   5) lee /metrics del orquestador (estado del circuito, rechazos del bulkhead),
#   6) comprueba que /health del gateway y el BFF siguen respondiendo.
# La evidencia queda en evidencia\escenario3\antes y evidencia\escenario3\despues.

$ErrorActionPreference = "Stop"
$raiz = Split-Path -Parent $PSScriptRoot
Set-Location $raiz

$gateway = "http://localhost:8080"
$token = if ($env:GATEWAY_TOKEN) { $env:GATEWAY_TOKEN } else { "token-clinica-123" }

# Parametros del experimento (faciles de explicar en la defensa).
$concurrentes = 12        # peticiones simultaneas con Pagos lento (mayor que BULKHEAD_MAX_CONCURRENT=5)
$latenciaLentaMs = 3000   # mayor que TIMEOUT_PAGOS_SECONDS=2, asi cada cobro lento vence por timeout
$secuenciales = 6         # peticiones una tras otra con Pagos caido
$baseline = 3             # peticiones con Pagos sano

# ---------------------------------------------------------------- utilidades

$script:archivo = $null
function Log($texto) {
    Write-Host $texto
    if ($script:archivo) { Add-Content -Path $script:archivo -Value $texto -Encoding utf8 }
}

# Ejecuta Python dentro de un servicio interno (no tienen puerto publicado).
# El codigo usa solo comillas simples para que PowerShell no lo rompa.
function Invoke-Interno($servicio, $codigo) {
    return (docker compose exec -T $servicio python -c $codigo)
}

function Get-InternoJson($servicio, $ruta) {
    $texto = Invoke-Interno $servicio "import urllib.request; print(urllib.request.urlopen('http://localhost:8000$ruta').read().decode())"
    return ($texto | ConvertFrom-Json)
}

# Cambia la latencia y la caida de la pasarela en caliente (sin reiniciar Pagos).
function Set-ChaosPagos([int]$latenciaMs, [bool]$falla) {
    $valor = if ($falla) { "True" } else { "False" }
    $codigo = "import urllib.request, json; r = urllib.request.Request('http://localhost:8000/admin/chaos', data=json.dumps({'latencia_ms': $latenciaMs, 'falla': $valor}).encode(), headers={'Content-Type': 'application/json'}); print(urllib.request.urlopen(r).read().decode())"
    Invoke-Interno "pagos" $codigo | Out-Null
}

# Agrega slots LIBRES extra directamente en la BD de Agenda: cada peticion necesita su propio
# slot (la regla "una hora, una mascota"), y el seed solo trae 4.
function Add-SlotsDePrueba([int]$cantidad) {
    $codigo = "import sqlite3, datetime; c = sqlite3.connect('/data/agenda.db'); n = c.execute('SELECT COUNT(*) FROM slots').fetchone()[0]; base = datetime.datetime(2027, 1, 1); c.executemany('INSERT INTO slots (veterinario, inicio, estado) VALUES (?, ?, ?)', [('Dr. Carga', str(base + datetime.timedelta(hours=n + i)), 'LIBRE') for i in range($cantidad)]); c.commit()"
    Invoke-Interno "agenda" $codigo | Out-Null
}

function Get-SlotsLibres {
    $slots = Get-InternoJson "agenda" "/slots"
    return @($slots | Where-Object { $_.estado -eq "LIBRE" } | ForEach-Object { $_.id })
}

# Espera a que el gateway y el orquestador respondan tras recrearlos.
function Wait-Sistema {
    for ($i = 0; $i -lt 40; $i++) {
        try {
            $r = Invoke-WebRequest -UseBasicParsing -Uri "$gateway/health" -TimeoutSec 2
            $m = Get-InternoJson "orquestador" "/metrics"
            if ($r.StatusCode -eq 200 -and $m) { return }
        } catch { }
        Start-Sleep -Seconds 2
    }
    throw "El sistema no volvio a responder"
}

# Una peticion POST /api/citas por el gateway; devuelve status, tiempo y estado de la saga.
function Invoke-Cita($slotId, $prefijo) {
    $headers = @{ Authorization = "Bearer $token"; "Idempotency-Key" = "$prefijo-" + [guid]::NewGuid().ToString() }
    $cuerpo = (@{ mascota_id = 1; slot_id = $slotId; monto = 15000 } | ConvertTo-Json)
    $reloj = [System.Diagnostics.Stopwatch]::StartNew()
    $status = 0
    $estado = "?"
    try {
        $r = Invoke-WebRequest -UseBasicParsing -Method Post -Uri "$gateway/api/citas" -Headers $headers `
            -ContentType "application/json" -Body $cuerpo -TimeoutSec 60
        $status = [int]$r.StatusCode
        $estado = ($r.Content | ConvertFrom-Json).estado
    } catch {
        if ($_.Exception.Response) { $status = [int]$_.Exception.Response.StatusCode } else { $status = -1 }
    }
    $reloj.Stop()
    return [pscustomobject]@{ status = $status; ms = [int]$reloj.ElapsedMilliseconds; saga = $estado }
}

# Lanza varias peticiones AL MISMO TIEMPO con Start-Job (un proceso por peticion).
function Invoke-CitasConcurrentes($slots, $prefijo) {
    $bloque = {
        param($gateway, $token, $slotId, $prefijo)
        $ProgressPreference = "SilentlyContinue"
        $headers = @{ Authorization = "Bearer $token"; "Idempotency-Key" = "$prefijo-" + [guid]::NewGuid().ToString() }
        $cuerpo = (@{ mascota_id = 1; slot_id = $slotId; monto = 15000 } | ConvertTo-Json)
        $reloj = [System.Diagnostics.Stopwatch]::StartNew()
        $status = 0
        $estado = "?"
        try {
            $r = Invoke-WebRequest -UseBasicParsing -Method Post -Uri "$gateway/api/citas" -Headers $headers `
                -ContentType "application/json" -Body $cuerpo -TimeoutSec 60
            $status = [int]$r.StatusCode
            $estado = ($r.Content | ConvertFrom-Json).estado
        } catch {
            if ($_.Exception.Response) { $status = [int]$_.Exception.Response.StatusCode } else { $status = -1 }
        }
        $reloj.Stop()
        [pscustomobject]@{ status = $status; ms = [int]$reloj.ElapsedMilliseconds; saga = $estado }
    }
    $trabajos = @()
    foreach ($slotId in $slots) {
        $trabajos += Start-Job -ScriptBlock $bloque -ArgumentList $gateway, $token, $slotId, $prefijo
    }
    $resultados = $trabajos | Wait-Job | Receive-Job
    $trabajos | Remove-Job
    return @($resultados)
}

# Resume una lista de resultados: tiempos y conteo por estado de saga.
function Show-Resumen($titulo, $resultados) {
    $tiempos = @($resultados | ForEach-Object { $_.ms })
    $prom = [int](($tiempos | Measure-Object -Average).Average)
    $max = ($tiempos | Measure-Object -Maximum).Maximum
    $min = ($tiempos | Measure-Object -Minimum).Minimum
    $sagas = ($resultados | Group-Object saga | ForEach-Object { "$($_.Name)=$($_.Count)" }) -join ", "
    Log "  $titulo : n=$($resultados.Count) | tiempo ms min=$min prom=$prom max=$max | sagas: $sagas"
    return [pscustomobject]@{ prom = $prom; max = $max }
}

function Show-Metricas($etiqueta) {
    $m = Get-InternoJson "orquestador" "/metrics"
    Log "  Metricas del orquestador ($etiqueta):"
    Log ("    " + ($m.resiliencia | ConvertTo-Json -Compress))
    return $m.resiliencia
}

# Comprueba que el resto del sistema sigue vivo mientras Pagos esta mal.
function Test-RestoDelSistema($etiqueta) {
    $okHealth = $false
    $okBff = $false
    $msHealth = 0
    $msBff = 0
    try {
        $t = [System.Diagnostics.Stopwatch]::StartNew()
        $r = Invoke-WebRequest -UseBasicParsing -Uri "$gateway/health" -TimeoutSec 10
        $msHealth = [int]$t.ElapsedMilliseconds
        $okHealth = ($r.StatusCode -eq 200)
    } catch { }
    try {
        $t = [System.Diagnostics.Stopwatch]::StartNew()
        $r = Invoke-WebRequest -UseBasicParsing -Uri "$gateway/api/bff/resumen-mascota/1" -Headers @{ Authorization = "Bearer $token" } -TimeoutSec 10
        $msBff = [int]$t.ElapsedMilliseconds
        $okBff = ($r.StatusCode -eq 200)
    } catch { }
    Log "  Resto del sistema ($etiqueta): GET /health -> $(if ($okHealth) { 'OK' } else { 'FALLA' }) ($msHealth ms) | GET /api/bff/resumen-mascota/1 -> $(if ($okBff) { 'OK' } else { 'FALLA' }) ($msBff ms)"
    return ($okHealth -and $okBff)
}

# ---------------------------------------------------------------- una corrida

function Invoke-Corrida($modo, $patrones) {
    $carpeta = Join-Path $raiz "evidencia\escenario3\$modo"
    New-Item -ItemType Directory -Force -Path $carpeta | Out-Null
    $script:archivo = Join-Path $carpeta "salida.txt"
    "" | Set-Content -Path $script:archivo -Encoding utf8

    Log "=================================================================="
    Log "CORRIDA '$modo' (BREAKER_ENABLED=$patrones, BULKHEAD_ENABLED=$patrones) - $(Get-Date -Format s)"
    Log "=================================================================="

    # compose lee estas variables del entorno al recrear los contenedores.
    $env:BREAKER_ENABLED = $patrones
    $env:BULKHEAD_ENABLED = $patrones
    # Sin esto el rate limiting del gateway (30/min) cortaria el experimento antes de tiempo.
    $env:RATE_LIMIT_PER_MINUTE = "1000"
    docker compose up -d --force-recreate orquestador api-gateway | Out-Null
    Wait-Sistema

    Set-ChaosPagos 0 $false
    Add-SlotsDePrueba 40
    $libres = Get-SlotsLibres
    $siguiente = 0

    # 1) Baseline con Pagos sano.
    Log "`n[1] BASELINE (Pagos sano)"
    $res = @()
    for ($i = 0; $i -lt $baseline; $i++) { $res += Invoke-Cita $libres[$siguiente++] "esc3-base" }
    $base = Show-Resumen "baseline" $res

    # 2) Pagos lento + peticiones concurrentes.
    Log "`n[2] PAGOS LENTO ($latenciaLentaMs ms) con $concurrentes peticiones concurrentes"
    Set-ChaosPagos $latenciaLentaMs $false
    $grupo = $libres[$siguiente..($siguiente + $concurrentes - 1)]
    $siguiente += $concurrentes
    $res = Invoke-CitasConcurrentes $grupo "esc3-lento"
    $lento = Show-Resumen "con Pagos lento" $res
    $metLento = Show-Metricas "tras fase lenta"
    $okLento = Test-RestoDelSistema "Pagos lento"

    # 3) Pagos caido + peticiones seguidas.
    Log "`n[3] PAGOS CAIDO con $secuenciales peticiones seguidas"
    Set-ChaosPagos 0 $true
    $res = @()
    for ($i = 0; $i -lt $secuenciales; $i++) { $res += Invoke-Cita $libres[$siguiente++] "esc3-caido" }
    $caido = Show-Resumen "con Pagos caido" $res
    $metCaido = Show-Metricas "tras fase caida"
    $okCaido = Test-RestoDelSistema "Pagos caido"

    # Dejamos Pagos sano para no afectar a nadie mas.
    Set-ChaosPagos 0 $false
    $metCaido | ConvertTo-Json | Set-Content -Path (Join-Path $carpeta "metricas_final.json") -Encoding utf8

    # Logs de resiliencia como evidencia (ademas de los tiempos y las metricas).
    $logs = @(docker compose logs --no-color orquestador 2>&1 | Select-String -Pattern 'circuit_breaker|fallback_pagos|bulkhead')
    $lineas = @("Logs del orquestador filtrados (circuit_breaker|fallback_pagos|bulkhead) - modo: $modo") + @($logs | ForEach-Object { $_.Line })
    $lineas | Set-Content -Path (Join-Path $carpeta "logs.txt") -Encoding utf8
    Log "  Logs guardados en evidencia\escenario3\$modo\logs.txt"

    return [pscustomobject]@{
        modo = $modo; base = $base; lento = $lento; caido = $caido
        estadoCircuito = $metCaido.estado_circuito
        rechazosCircuito = $metCaido.llamadas_rechazadas_circuito
        rechazosBulkhead = $metCaido.llamadas_rechazadas_bulkhead
        sistemaVivo = ($okLento -and $okCaido)
    }
}

# ---------------------------------------------------------------- programa principal

try {
    $antes = Invoke-Corrida "antes" "false"
    $despues = Invoke-Corrida "despues" "true"
}
finally {
    # Dejamos el entorno con los patrones activos (valor por defecto del proyecto).
    Remove-Item Env:BREAKER_ENABLED, Env:BULKHEAD_ENABLED, Env:RATE_LIMIT_PER_MINUTE -ErrorAction SilentlyContinue
}

$comparacion = Join-Path $raiz "evidencia\escenario3\comparacion.txt"
$script:archivo = $comparacion
"" | Set-Content -Path $comparacion -Encoding utf8
Log "=================================================================="
Log "COMPARACION ANTES vs DESPUES"
Log "=================================================================="
Log ("{0,-34} {1,12} {2,12}" -f "", "antes", "despues")
Log ("{0,-34} {1,12} {2,12}" -f "baseline prom (ms)", $antes.base.prom, $despues.base.prom)
Log ("{0,-34} {1,12} {2,12}" -f "Pagos lento: prom (ms)", $antes.lento.prom, $despues.lento.prom)
Log ("{0,-34} {1,12} {2,12}" -f "Pagos lento: max (ms)", $antes.lento.max, $despues.lento.max)
Log ("{0,-34} {1,12} {2,12}" -f "Pagos caido: prom (ms)", $antes.caido.prom, $despues.caido.prom)
Log ("{0,-34} {1,12} {2,12}" -f "Estado final del circuito", $antes.estadoCircuito, $despues.estadoCircuito)
Log ("{0,-34} {1,12} {2,12}" -f "Rechazos por circuito abierto", $antes.rechazosCircuito, $despues.rechazosCircuito)
Log ("{0,-34} {1,12} {2,12}" -f "Rechazos por bulkhead", $antes.rechazosBulkhead, $despues.rechazosBulkhead)
Log ("{0,-34} {1,12} {2,12}" -f "Gateway y BFF siguen respondiendo", $antes.sistemaVivo, $despues.sistemaVivo)
Log "`nEvidencia en evidencia\escenario3\antes, \despues y comparacion.txt"
