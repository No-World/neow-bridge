Get-Process java -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 3
$net = netstat -an | Select-String "37771"
if ($net) {
    Write-Host "PORT STILL IN USE:"
    Write-Host $net
} else {
    Write-Host "PORT FREE"
    Set-Location "D:\Users\NoWorld\Documents\source\Neow\neow-bridge\runtime"
    $env:NEOW_BRIDGE_PORT = "37771"
    $env:NEOW_BRIDGE_BIND = "127.0.0.1"
    Start-Process -FilePath ".\jre\bin\java.exe" `
        -ArgumentList "-Xmx1G","-jar","ModTheSpire.jar","--mods","basemod,neow-bridge","--skip-launcher"
    Write-Host "GAME LAUNCHED"
    Start-Sleep -Seconds 25
    $net2 = netstat -an | Select-String "37771"
    if ($net2) { Write-Host "SERVER LISTENING:"; Write-Host $net2 } else { Write-Host "SERVER NOT YET LISTENING" }
}
