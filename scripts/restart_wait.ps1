Write-Host "=== STEP 1: Kill all java ==="
Get-Process java -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 5

Write-Host "=== STEP 2: Wait for port release ==="
$maxWait = 30
$waited = 0
while ($waited -lt $maxWait) {
    $net = netstat -an | Select-String "37771"
    if (-not $net) {
        Write-Host "Port free after $waited seconds"
        break
    }
    Start-Sleep -Seconds 1
    $waited++
}
if ($waited -ge $maxWait) {
    Write-Host "Port still busy after $maxWait seconds. Aborting."
    exit 1
}

Write-Host "=== STEP 3: Launch game ==="
Set-Location "D:\Users\NoWorld\Documents\source\Neow\neow-bridge\runtime"
$env:NEOW_BRIDGE_PORT = "37771"
$env:NEOW_BRIDGE_BIND = "127.0.0.1"

$proc = Start-Process -FilePath ".\jre\bin\java.exe" `
    -ArgumentList "-Xmx1G","-jar","ModTheSpire.jar","--mods","basemod,neow-bridge","--skip-launcher" `
    -PassThru `
    -RedirectStandardOutput "game_log.txt" `
    -RedirectStandardError "game_err.txt"

Write-Host "Java PID: $($proc.Id)"

Write-Host "=== STEP 4: Wait for server ==="
for ($i = 1; $i -le 60; $i++) {
    Start-Sleep -Seconds 2
    $net = netstat -an | Select-String "37771.*LISTENING"
    if ($net) {
        Write-Host "SERVER LISTENING after $((2*$i)) seconds!"
        Write-Host $net
        break
    }
    if ($i % 5 -eq 0) {
        Write-Host "waiting... ${i}x2 = $($i*2)s"
    }
    if ($proc.HasExited) {
        Write-Host "PROCESS EXITED unexpectedly!"
        exit 1
    }
}
Write-Host "=== DONE ==="
