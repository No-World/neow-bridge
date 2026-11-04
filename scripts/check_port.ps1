Write-Host "=== Java Process ==="
Get-Process java -ErrorAction SilentlyContinue | Select-Object Id, WorkingSet64 | Format-Table
Write-Host "=== Port 37771 ==="
netstat -an | Select-String "37771"
Write-Host "=== TCP Test ==="
try {
    $tcp = New-Object System.Net.Sockets.TcpClient("127.0.0.1", 37771)
    Write-Host "CONNECTED"
    $tcp.Close()
} catch {
    Write-Host "NOT CONNECTED"
}
