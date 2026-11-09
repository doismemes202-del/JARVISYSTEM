# Inicia o THE JARVIS SYSTEM na CLI (garante que o Ollama está de pé)
#   .\start_jarvis.ps1          → CLI
#   .\start_jarvis.ps1 -Web     → CLI + terminal web para o celular
param([switch]$Web, [switch]$Voice)
Set-Location $PSScriptRoot
try { Invoke-RestMethod http://127.0.0.1:11434/api/version -TimeoutSec 2 | Out-Null }
catch {
    Write-Host "Iniciando Ollama..." -ForegroundColor Yellow
    Start-Process ollama -ArgumentList "serve" -WindowStyle Hidden
    Start-Sleep 3
}
$cliArgs = @()
if ($Web) { $cliArgs += "--web" }
if ($Voice) { $cliArgs += "--voice" }
& "$PSScriptRoot\.venv\Scripts\python.exe" "$PSScriptRoot\jarvis_cli.py" @cliArgs
