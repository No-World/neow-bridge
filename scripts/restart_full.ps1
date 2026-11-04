Write-Host "=== Kill all java ==="
Get-Process java -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 5

Write-Host "=== Check port ==="
$net = netstat -an | Select-String "37771"
if ($net) {
    Write-Host "Port busy, waiting..."
    Start-Sleep -Seconds 10
}

Write-Host "=== Launch game ==="
Set-Location "D:\Users\NoWorld\Documents\source\Neow\neow-bridge\runtime"
$env:NEOW_BRIDGE_PORT = "37771"
$env:NEOW_BRIDGE_BIND = "127.0.0.1"
Start-Process -FilePath ".\jre\bin\java.exe" `
    -ArgumentList "-Xmx1G","-jar","ModTheSpire.jar","--mods","basemod,neow-bridge","--skip-launcher"

Write-Host "=== Wait for bind ==="
for ($i = 1; $i -le 30; $i++) {
    Start-Sleep -Seconds 3
    $net = netstat -an | Select-String "37771.*LISTENING"
    if ($net) {
        Write-Host "SERVER UP after $($i*3)s: $net"
        break
    }
    if ($i % 5 -eq 0) { Write-Host "  ${i}x3 = $($i*3)s..." }
}

Write-Host "=== TCP Test ==="
try {
    $tcp = New-Object System.Net.Sockets.TcpClient("127.0.0.1", 37771)
    Write-Host "TCP CONNECTED!"
    $tcp.Close()
} catch {
    Write-Host "TCP FAIL: $($_.Exception.Message)"
}
Write-Host "=== DONE ==="
