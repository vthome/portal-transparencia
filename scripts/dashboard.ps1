<#
.SYNOPSIS
  Abre o dashboard (Streamlit) no navegador.

.EXAMPLE
  .\scripts\dashboard.ps1                                                   # API local (http://127.0.0.1:8000)
  .\scripts\dashboard.ps1 -Api https://monitor-transparencia-api.onrender.com   # API publicada no Render
#>
param([string]$Api = "", [int]$Porta = 8501)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
$py = "python"
if (-not $env:VIRTUAL_ENV) {
  foreach ($v in @(".venv", "mprj-venv", "..\mprj-venv")) {
    if (Test-Path "$v\Scripts\python.exe") { $py = "$v\Scripts\python.exe"; break }
  }
}
$env:PYTHONIOENCODING = "utf-8"; $env:PYTHONUTF8 = "1"
& $py -c "import streamlit, plotly" 2>$null
if ($LASTEXITCODE -ne 0) { Write-Host "Instalando dependências do dashboard..." -ForegroundColor Yellow; & $py -m pip install -r dashboard\requirements.txt }
if ($Api) { $env:API_URL = $Api }      # tem prioridade sobre o .streamlit\secrets.toml
Write-Host "Dashboard: http://localhost:$Porta  (Ctrl+C para parar)" -ForegroundColor Cyan
& $py -m streamlit run dashboard\app.py --server.port $Porta
