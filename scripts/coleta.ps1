<#
.SYNOPSIS
  Rodada completa da Camada 1 (coleta + extração) gravando no Supabase. Windows/PowerShell.

.EXAMPLE
  .\scripts\coleta.ps1                                  # preflight + todos os entes
  .\scripts\coleta.ps1 -Entes rj-marica -MaxPaginas 20  # um ente, orçamento menor
  .\scripts\coleta.ps1 -SemRender                       # sem Chromium (SPAs ficam 'vazio')
  .\scripts\coleta.ps1 -SoPreflight                     # só verifica o ambiente
  .\scripts\coleta.ps1 -Local                           # sem Supabase, grava em data\
#>
param(
  [string]$Entes = "",
  [int]$MaxPaginas = 0,
  [int]$Paralelismo = 0,
  [switch]$SemRender,
  [switch]$Local,
  [switch]$PularPreflight,
  [switch]$SoPreflight,
  [switch]$SemRede
)
$ErrorActionPreference = "Stop"
$raiz = Split-Path -Parent $PSScriptRoot
Set-Location $raiz

# venv do projeto, se existir
$py = "python"
if (Test-Path ".venv\Scripts\python.exe") { $py = ".venv\Scripts\python.exe" }

# saída em UTF-8 (acentos nos logs e no relatório)
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

if (-not (Test-Path ".env") -and -not $Local) {
  Write-Host "Arquivo .env não encontrado. Copie .env.example para .env e preencha." -ForegroundColor Red
  exit 1
}

if (-not $PularPreflight -and -not $Local) {
  Write-Host "`n== Verificação prévia ==" -ForegroundColor Cyan
  $pf = @("-m", "ingestion.preflight")
  if ($SemRender) { $pf += "--sem-render" }
  if ($SemRede)   { $pf += "--sem-rede" }
  if ($Entes)     { $pf += @("--entes", $Entes) }
  & $py @pf
  if ($LASTEXITCODE -ne 0) {
    Write-Host "`nPreflight falhou: corrija os itens [ERRO] acima antes de coletar." -ForegroundColor Red
    exit $LASTEXITCODE
  }
  if ($SoPreflight) { exit 0 }
}

Write-Host "`n== Coleta + extração ==" -ForegroundColor Cyan
$args_ = @("-m", "ingestion.pipeline")
if ($Entes)            { $args_ += @("--entes", $Entes) }
if ($MaxPaginas -gt 0) { $args_ += @("--max-paginas", $MaxPaginas) }
if ($Paralelismo -gt 0){ $args_ += @("--paralelismo", $Paralelismo) }
if ($SemRender)        { $args_ += "--sem-render" }
if ($Local)            { $args_ += "--local" }
& $py @args_
$codigo = $LASTEXITCODE

Write-Host "`n== Relatório ==" -ForegroundColor Cyan
$rel = Get-ChildItem "data\runs\*-relatorio.md" -ErrorAction SilentlyContinue |
       Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($rel) { Get-Content $rel.FullName -Encoding UTF8; Write-Host "`n(salvo em $($rel.FullName))" }
exit $codigo
