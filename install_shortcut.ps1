# Cria os atalhos "J.A.R.V.I.S" (Menu Iniciar + Área de Trabalho) com o ícone do reator arc.
# Depois: botão direito no atalho → "Fixar na barra de tarefas".
#   powershell -ExecutionPolicy Bypass -File .\install_shortcut.ps1
#   powershell -ExecutionPolicy Bypass -File .\install_shortcut.ps1 -NoWeb
param([switch]$NoWeb)
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$ico = Join-Path $root "assets\jarvis.ico"
if (-not (Test-Path $ico)) { & "$root\.venv\Scripts\python.exe" "$root\tools\make_icon.py" }

$launch = "-NoLogo -ExecutionPolicy Bypass -File `"$root\start_jarvis.ps1`"" + $(if ($NoWeb) { "" } else { " -Web" })
$wt = (Get-Command wt.exe -ErrorAction SilentlyContinue).Source
if ($wt) {
    # Windows Terminal: emojis, truecolor e bordas arredondadas renderizam corretamente
    $target = $wt
    $arguments = "--title `"J.A.R.V.I.S`" --tabColor `"#00e5ff`" -d `"$root`" powershell.exe $launch"
} else {
    $target = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
    $arguments = $launch
}

$shell = New-Object -ComObject WScript.Shell
$places = @(
    (Join-Path ([Environment]::GetFolderPath("Programs")) "J.A.R.V.I.S.lnk"),
    (Join-Path ([Environment]::GetFolderPath("Desktop")) "J.A.R.V.I.S.lnk")
)
foreach ($p in $places) {
    $lnk = $shell.CreateShortcut($p)
    $lnk.TargetPath = $target
    $lnk.Arguments = $arguments
    $lnk.WorkingDirectory = $root
    $lnk.IconLocation = "$ico,0"
    $lnk.Description = "THE JARVIS SYSTEM — CLI (F1 = parada de emergência)"
    $lnk.Save()
    Write-Host "Atalho criado: $p" -ForegroundColor Cyan
}
Write-Host "`nPara fixar: Iniciar → digite JARVIS → botão direito → Fixar na barra de tarefas." -ForegroundColor Green
