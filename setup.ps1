# THE JARVIS SYSTEM — instalação completa no Windows 11 (PowerShell)
#   powershell -ExecutionPolicy Bypass -File .\setup.ps1            # tudo
#   powershell -ExecutionPolicy Bypass -File .\setup.ps1 -SkipVoice # sem pipeline de voz
param(
    [switch]$SkipVoice,
    [switch]$SkipModels,
    [switch]$SkipOI,
    # Base do JARVIS e da FRIDAY (a mesma para os dois: um único modelo fica na VRAM).
    # "qwen2.5:7b" é a opção leve antiga, para GPUs com menos de 10 GB.
    [string]$BaseModel = "qwen3.5:9b"
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
function Step($t) { Write-Host "`n=== $t ===" -ForegroundColor Cyan }

# 1) Ambiente Python -----------------------------------------------------------
Step "Ambiente virtual Python"
if (-not (Test-Path .venv)) { python -m venv .venv }
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
& $py -m pip install --upgrade pip
& $py -m pip install rich prompt_toolkit fastapi "uvicorn[standard]" websockets psutil nvidia-ml-py keyboard requests gspread google-auth pywhatkit sympy

if (-not $SkipVoice) {
    Step "Pipeline de voz (openWakeWord + Faster-Whisper CUDA + Piper)"
    & $py -m pip install -r requirements-voice.txt
    & $py -c "import openwakeword.utils as u; u.download_models(model_names=['hey_jarvis'])"
    New-Item -ItemType Directory -Force voices | Out-Null
    $base = "https://huggingface.co/rhasspy/piper-voices/resolve/main/pt/pt_BR/faber/medium"
    foreach ($f in "pt_BR-faber-medium.onnx", "pt_BR-faber-medium.onnx.json") {
        if (-not (Test-Path "voices\$f")) { Invoke-WebRequest "$base/$f" -OutFile "voices\$f" }
    }
}

if (-not $SkipOI) {
    Step "Open Interpreter (venv separado em Python 3.11)"
    # O open-interpreter depende de um tiktoken antigo sem wheel para Python 3.13 e do pkg_resources
    # (removido no setuptools 81+). Por isso ele roda num venv 3.11 próprio, criado pelo uv.
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        winget install --id astral-sh.uv -e --accept-source-agreements
        $env:Path = "$env:LOCALAPPDATA\Microsoft\WinGet\Links;$env:USERPROFILE\.local\bin;$env:Path"
    }
    if (-not (Test-Path .venv-oi)) { uv venv --python 3.11 .venv-oi }
    uv pip install --python .venv-oi\Scripts\python.exe open-interpreter "setuptools<81"
    $oiPy = (Resolve-Path .venv-oi\Scripts\python.exe).Path
    if (-not (Test-Path .env)) { Copy-Item .env.example .env }
    if (-not (Select-String -Path .env -Pattern "^OI_PYTHON=.+" -Quiet)) {
        (Get-Content .env) -replace "^OI_PYTHON=.*$", "OI_PYTHON=$oiPy" | Set-Content .env -Encoding utf8
    }
}

# 2) Modelos Ollama -------------------------------------------------------------
if (-not $SkipModels) {
    Step "Modelos Ollama"
    if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) { throw "Ollama não encontrado. Instale em https://ollama.com" }
    try { Invoke-RestMethod http://127.0.0.1:11434/api/version | Out-Null }
    catch { Start-Process ollama -ArgumentList "serve" -WindowStyle Hidden; Start-Sleep 4 }

    ollama pull $BaseModel
    ollama pull qwen2.5vl:7b
    foreach ($name in "Jarvis", "Friday") {
        $mf = (Get-Content "Modelfile_$name" -Raw) -replace "(?m)^FROM .*$", "FROM $BaseModel"
        $tmp = Join-Path $env:TEMP "Modelfile_$name.tmp"
        Set-Content $tmp $mf -Encoding utf8
        ollama create $name.ToLower() -f $tmp
    }
    ollama list
}

# 3) Config -----------------------------------------------------------------------
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
if (-not (Test-Path data\contacts.json)) { Copy-Item data\contacts.example.json data\contacts.json }
New-Item -ItemType Directory -Force credentials | Out-Null

Step "Firewall (acesso pelo celular na rede local)"
Write-Host "Execute como Administrador, uma única vez:" -ForegroundColor Yellow
Write-Host '  New-NetFirewallRule -DisplayName "JARVIS 8000" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow -Profile Private'

Step "Pronto"
Write-Host "Inicie com:  .\start_jarvis.ps1        (CLI)" -ForegroundColor Green
Write-Host "        ou:  .\start_jarvis.ps1 -Web   (CLI + terminal web no celular)" -ForegroundColor Green
