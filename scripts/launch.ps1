# NeowBridge game launcher (Windows-owned process, independent of WSL lifetime).
# NOTE: keep this file ASCII-only. PowerShell 5.1 parses BOM-less files as ANSI.
#
# The game window is moved OFF-SCREEN after launch (not minimized!):
#   - minimized/hidden windows freeze the GL driver (glGetIntegerv deadlock) and
#     libgdx stops rendering -> the bridge hooks die.
#   - NOTE: off-screen does NOT work (DWM stops compositing -> GL stalls like minimized).
#     The window is placed at the bottom-right corner ON-SCREEN, small, never minimized.
# NEVER minimize the game window while automation is running.
param(
    [string]$RtDir,
    [string]$JavaExe,
    [int]$Port = 37771,
    [string]$Bind = "127.0.0.1",
    [switch]$Hidden
)
$env:NEOW_BRIDGE_PORT = "$Port"
# WSL 环境变量不会自动传到 Windows 进程（需 WSLENV），这里显式转发洗牌追踪开关
if ($env:NEOW_TRACE_SHUFFLE) { $env:NEOW_TRACE_SHUFFLE = $env:NEOW_TRACE_SHUFFLE }
if ($env:NEOW_TRACE_RNG) { $env:NEOW_TRACE_RNG = $env:NEOW_TRACE_RNG }

$env:NEOW_BRIDGE_BIND = $Bind
Start-Process -FilePath $JavaExe `
    -ArgumentList '-Xmx2048m','-jar','ModTheSpire.jar','--mods','basemod,neow-bridge','--skip-launcher' `
    -WorkingDirectory $RtDir `
    -WindowStyle $(if ($Hidden) { "Hidden" } else { "Normal" }) `
    -RedirectStandardOutput (Join-Path $RtDir 'game.log') `
    -RedirectStandardError (Join-Path $RtDir 'game.err.log')

if (-not $Hidden) {
    # Move the game window off-screen once it appears (up to ~40s).
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type @'
using System; using System.Runtime.InteropServices;
public class NeowW { [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr h, IntPtr a, int x, int y, int cx, int cy, uint f); }
'@
    for ($i = 0; $i -lt 40; $i++) {
        Start-Sleep 1
        $p = Get-Process java -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
        if ($p) {
            Start-Sleep 2
            $wa = [System.Windows.Forms.Screen]::PrimaryScreen.WorkingArea
            $x = $wa.Right - 1024; $y = $wa.Bottom - 660
            [NeowW]::SetWindowPos($p.MainWindowHandle, [IntPtr]::Zero, $x, $y, 1024, 640, 0x0040) | Out-Null
            Write-Output "window moved off-screen"
            break
        }
    }
}
Write-Output "launched"
