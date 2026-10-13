<#
.SYNOPSIS
  Camada 2 (embeddings + conformidade + score) sobre a última rodada de coleta, gravando no Supabase.

.EXAMPLE
  .\scripts\conclusao.ps1                              # última rodada, todos os entes, modelo SERAFIM 768
  .\scripts\conclusao.ps1 -Run 7 -Entes rj-marica      # uma rodada/um ente
  .\scripts\conclusao.ps1 -SemEmbeddings               # só regras (quesitos semânticos caem para termos)
  .\scripts\conclusao.ps1 -Embedder hash               # testa o encanamento sem baixar o modelo
  .\scripts\conclusao.ps1 -Forcar                      # substitui avaliação que já tem revisão humana
  .\scripts\conclusao.ps1 -Local                       # sobre data\local (coleta feita com -Local)
#>
param(
  [string]$Run = "",
  [string]$Entes = "",
  [ValidateSet("modelo", "hash")][string]$Embedder = "modelo",
  [switch]$SemEmbeddings,
  [switch]$Forcar,
  [switch]$Local,
  [int]$ManterRodadas = 2
)
$ErrorActionPreference = "Stop"
$raiz = Split-Path -Parent $PSScriptRoot
Set-Location $raiz

$py = "python"
if (-not $env:VIRTUAL_ENV) {
  foreach ($v in @(".venv", "mprj-venv", "..\mprj-venv")) {
    if (Test-Path "$v\Scripts\python.exe") { $py = "$v\Scripts\python.exe"; break }
  }
}
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

if (-not (Test-Path ".env") -and -not $Local) {
  Write-Host "Arquivo .env não encontrado. Copie .env.example para .env e preencha." -ForegroundColor Red
  exit 1
}

if (-not $SemEmbeddings -and $Embedder -eq "modelo") {
  & $py -c "import sentence_transformers" 2>$null
  if ($LASTEXITCODE -ne 0) {
    Write-Host "sentence-transformers não instalado. Rode:" -ForegroundColor Yellow
    Write-Host "  pip install torch --index-url https://download.pytorch.org/whl/cpu"
    Write-Host "  pip install -r requirements-ml.txt"
    Write-Host "ou use -SemEmbeddings / -Embedder hash." -ForegroundColor Yellow
    exit 1
  }
}

Write-Host "`n== Camada 2: embeddings, conformidade e score ==" -ForegroundColor Cyan
$a = @("-m", "compliance.pipeline", "--embedder", $Embedder, "--manter-rodadas", $ManterRodadas)
if ($Run)           { $a += @("--run", $Run) }
if ($Entes)         { $a += @("--entes", $Entes) }
if ($SemEmbeddings) { $a += "--sem-embeddings" }
if ($Forcar)        { $a += "--forcar" }
if ($Local)         { $a += "--local" }
& $py @a
$codigo = $LASTEXITCODE
$rel = Get-ChildItem "data\runs\*-conformidade.md" -ErrorAction SilentlyContinue |
       Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($rel) { Write-Host "`n(relatório salvo em $($rel.FullName))" -ForegroundColor Cyan }
exit $codigo
