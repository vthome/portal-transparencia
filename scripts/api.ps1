<#
.SYNOPSIS
  Sobe a API localmente (lê DATABASE_URL do .env) e/ou roda o teste de fumaça.

.EXAMPLE
  .\scripts\api.ps1                                   # http://127.0.0.1:8000/docs
  .\scripts\api.ps1 -Testar                           # sobe, testa e deixa rodando
  .\scripts\api.ps1 -Url https://monitor-transparencia-api.onrender.com   # só testa a API publicada
#>
param(
  [int]$Porta = 8000,
  [switch]$Testar,
  [string]$Url = "",
  [string]$Token = ""
)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
$py = "python"
if (-not $env:VIRTUAL_ENV) {
  foreach ($v in @(".venv", "mprj-venv", "..\mprj-venv")) {
    if (Test-Path "$v\Scripts\python.exe") { $py = "$v\Scripts\python.exe"; break }
  }
}
$env:PYTHONIOENCODING = "utf-8"; $env:PYTHONUTF8 = "1"

& $py -c "import fastapi, uvicorn, psycopg_pool" 2>$null
if ($LASTEXITCODE -ne 0) { Write-Host "Instalando dependências da API..." -ForegroundColor Yellow; & $py -m pip install -r requirements-api.txt }

$teste = @("scripts\testar_api.py")
if ($Token) { $teste += @("--token", $Token) }

if ($Url) { & $py @($teste + @("--url", $Url)); exit $LASTEXITCODE }

if ($Testar) {
  $srv = Start-Process $py -ArgumentList "-m uvicorn api.main:app --port $Porta" -PassThru -NoNewWindow
  Start-Sleep -Seconds 5
  & $py @($teste + @("--url", "http://127.0.0.1:$Porta"))
  Write-Host "`nAPI rodando em http://127.0.0.1:$Porta/docs (Ctrl+C ou feche a janela para parar)" -ForegroundColor Cyan
  Wait-Process -Id $srv.Id
} else {
  Write-Host "Swagger: http://127.0.0.1:$Porta/docs" -ForegroundColor Cyan
  & $py -m uvicorn api.main:app --reload --port $Porta
}
