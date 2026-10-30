# NeowBridge game launcher (Windows-owned process, independent of WSL lifetime).
# NOTE: keep this file ASCII-only. PowerShell 5.1 parses BOM-less files as ANSI.
param(
    [string]$RtDir,
    [string]$JavaExe,
    [int]$Port = 37771,
    [string]$Bind = "127.0.0.1",
    [switch]$Visible
)
$env:NEOW_BRIDGE_PORT = "$Port"
$env:NEOW_BRIDGE_BIND = $Bind
Start-Process -FilePath $JavaExe `
    -ArgumentList '-Xmx2048m','-jar','ModTheSpire.jar','--mods','basemod,neow-bridge','--skip-launcher' `
    -WorkingDirectory $RtDir `
    -WindowStyle $(if ($Visible) { "Normal" } else { "Hidden" }) `
    -RedirectStandardOutput (Join-Path $RtDir 'game.log') `
    -RedirectStandardError (Join-Path $RtDir 'game.err.log')
Write-Output "launched"
