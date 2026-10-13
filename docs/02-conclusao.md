# Camada 2 — Conclusão: embeddings, conformidade e score

Lê os snapshots de uma rodada de coleta (nunca acessa os portais) e grava no Supabase, schema `monitor`:

```
page_snapshot / file_artifact ──► trechos ──► embeddings 768 ──► monitor.chunk
            │                                        │
            └──► 142 quesitos × 13 verificadores ◄───┘ (busca semântica)
                         │
                         ▼
        criterion_result (status, nota, confiança, método, sinais) + evidence
                         │
                         ▼
        evaluation (score, faixa min–max, cobertura, essenciais, classificação, scores por dimensão)
```

Como regras e coleta são separadas, mudar `criteria.yaml`, `scoring.yaml` ou um verificador e rodar de novo **reavalia rodadas antigas sem recoletar** (`--run N`).

## 1. Antes da primeira execução

1. **Supabase → SQL Editor:** rode o `sql/schema.sql` inteiro de novo (v0.3, idempotente). Ele:
   - converte `monitor.chunk.embedding` de `vector(512)` para **`vector(768)`** e recria o índice HNSW;
   - adiciona `chunk.texto_hash` (reuso de vetores entre rodadas), `criterion.params_hash`,
     `evaluation.engine_version`/`modelo_embedding`, `criterion_result.sinais` e a view `v_resultado_atual`.

   A conversão só acontece com `monitor.chunk` vazia (vetores de dimensões diferentes não são comparáveis). Se já houver linhas, o script para com a mensagem `rode "truncate monitor.chunk"` — rode o truncate e o schema de novo; os trechos são recalculados na próxima conclusão.
2. **Dependências:**
   ```powershell
   pip install -r requirements.txt                                       # numpy, scikit-learn
   pip install torch --index-url https://download.pytorch.org/whl/cpu   # PyTorch só CPU (bem menor)
   pip install -r requirements-ml.txt                                    # sentence-transformers
   ```
3. **`.env`:** acrescente as variáveis `EMBEDDINGS_*` do `.env.example`.
4. `python -m ingestion.preflight` agora mostra `schema v0.3 (Camada 2)` e `dimensão dos embeddings`.

## 2. Executando

```powershell
.\scripts\conclusao.ps1                          # última rodada, todos os entes
.\scripts\conclusao.ps1 -Run 7 -Entes rj-marica
.\scripts\conclusao.ps1 -SemEmbeddings           # só regras: quesitos semânticos usam termos
.\scripts\conclusao.ps1 -Embedder hash           # testa o encanamento sem baixar o modelo
.\scripts\conclusao.ps1 -Forcar                  # substitui avaliação que já tem revisão humana
```

Equivalente: `python -m compliance.pipeline [--run N] [--entes a,b] [--sem-embeddings] [--embedder hash] [--forcar] [--manter-rodadas 2] [--local]`.

Saída: JSON com o resumo por ente e o relatório `data/runs/<run>-conformidade.md` (ranking, scores por dimensão, contagem de status, fila de revisão, essenciais reprovados, amostra de evidências).

No GitHub, `.github/workflows/conclusao.yml` roda sozinho ao fim de cada coleta agendada (também manual, com `run`, `entes`, `embedder` e `forcar`). Precisa só do secret `DATABASE_URL`; o modelo fica em cache entre execuções.

**Reexecução da mesma rodada** substitui a avaliação (apaga e regrava em transação). Se algum resultado já tiver revisão humana, o pipeline para — use `--forcar` conscientemente: as revisões daquela avaliação são apagadas junto.

## 3. Sentence-transformer de 768 dimensões — o que muda

Sim, precisou mudar configuração. Resumo:

| Onde | Antes | Agora |
| --- | --- | --- |
| `monitor.chunk.embedding` | `vector(512)` | `vector(768)` (schema v0.3) |
| `.env` | — | `EMBEDDINGS_MODELO`, `EMBEDDINGS_DIM=768` |
| Modelo padrão | — | `PORTULAN/serafim-100m-portuguese-pt-sentence-encoder-ir` (768, BERT, PT, MIT) |
| Tamanho do trecho | — | 480 caracteres, sobreposição 100 |

- **Validação em três pontos:** o código compara a dimensão do modelo carregado com `EMBEDDINGS_DIM` e com a coluna do banco (`atttypmod`) e para **antes de gravar** se qualquer um divergir (`DimensaoIncompativel`).
- **Limite de 128 tokens:** o SERAFIM 100M lê no máximo 128 tokens por trecho e trunca o resto em silêncio. Por isso os trechos têm ~480 caracteres (≈ 120 tokens em português).
- **Alternativa multilíngue, também 768:** `sentence-transformers/paraphrase-multilingual-mpnet-base-v2`. Trocar de modelo com a mesma dimensão não exige mexer no banco: trechos são chaveados por `modelo`, então os novos vetores convivem com os antigos e o motor só usa os do modelo atual.
- **Modelo com outra dimensão** (ex.: 384, 1024): edite `vector(768)` no `sql/schema.sql` (duas ocorrências e o `768` do bloco 6.2), `truncate monitor.chunk`, rode o schema e ajuste `EMBEDDINGS_DIM`.
- **Limiares semânticos** (`scoring.yaml → semantico.alto/medio`) dependem do modelo; ao trocá-lo, confira-os contra uma amostra revisada.
- Vetores são normalizados (cosseno = produto interno); o índice é `hnsw (embedding vector_cosine_ops)`.

## 4. Embeddings

- `ingestion/transform/prepare_documents.py` divide cada página `ok` em janelas, prefixadas com `título | seções` para dar contexto. Planilhas/CSV/JSON viram um trecho descritivo (colunas + amostra) — buscar sobre linhas soltas não ajuda.
- Cada trecho tem `texto_hash = sha256(modelo + texto)`. Numa nova rodada, o texto que não mudou **reaproveita o vetor** sem recalcular (só busca no banco). A cada execução os trechos de rodadas além das `--manter-rodadas` (padrão 2) são apagados.
- O motor faz a busca semântica em memória (numpy) sobre os trechos do ente; o índice HNSW no banco fica para a busca da API/dashboard (Camada 3).

## 5. Checklist e verificadores

`compliance/config/criteria.yaml` é gerado do `checklistPortalTransp.xlsx` por `python scripts/importar_checklist.py` (o mapa quesito → verificador está no script). 142 quesitos: 122 da matriz comum + 20 da matriz específica do Executivo (não aplicáveis a Câmaras).

| Verificador | Quesitos | O que olha |
| --- | --- | --- |
| `portal` | 1 | site e portal acessíveis, domínio `.gov.br` |
| `homepage_link` | 1 | link para o portal no cabeçalho/menu da capa (+ screenshot como evidência) |
| `existence` | 25 | página/conteúdo por rótulo, seção, texto ou padrão (endereço, telefone, e-mail, horário) |
| `structured_fields` | 32 | campos exigidos nos cabeçalhos de tabelas HTML e colunas de CSV/XLSX |
| `search_filter` | 11 | formulários de consulta com filtros |
| `export` | 8 | exportação em formato aberto/legível por máquina |
| `freshness` | 12 | data mais recente (atualizado em, período do RREO/RGF…) vs. prazo |
| `history` | 12 | exercícios anteriores disponíveis (títulos, links, seletores de ano) |
| `document` | 24 | documentos exigidos (LOA, LDO, PPA, RREO, RGF, parecer…) |
| `semantic` | 8 | quesitos de linguagem livre: termos + busca por embeddings |
| `canal_eletronico` | 2 | e-SIC / ouvidoria acessíveis |
| `formulario_esic` | 1 | pedido de informação sem exigir motivo (LAI art. 10 §3º) |
| `accessibility` | 4 | barra de acessibilidade, alto contraste, fonte, migalhas |
| `pendente` | 1 | 16.5 (avaliação AccessMonitor) — não automatizado |

**Regra central:** falha de coleta nunca vira "não atende". Se a seção estava bloqueada por robots.txt, deu erro, o portal não foi coletado ou a informação só aparece após uma consulta, o resultado é `nao_verificavel` — sai do score e alarga a faixa de incerteza. "Não atende" com seção não encontrada tem confiança baixa e vai para revisão.

## 6. Confiança

Cada verificador devolve sinais: `forca` (qualidade da evidência: arquivo baixado > link > texto solto), `fontes` (evidências independentes, até 3) e `qualidade` (fração da seção coletada sem erro). A confiança é uma logística desses sinais:

```
confiança = σ(−3,0 + 3,2·força + 0,35·fontes + 1,2·qualidade)
```

Resultados com confiança < 0,6 entram na fila de revisão (`scoring.yaml → revisao_abaixo_de`). Com ≥ 30 revisões humanas em `monitor.human_review`, `python -m compliance.calibrar` ajusta os pesos por regressão logística (alvo: o revisor concordou com o motor) e grava `compliance/config/confianca.json`, usado automaticamente dali em diante.

## 7. Score

- Nota: atende 1 · parcial 0,5 · não atende 0. Peso: 3 essencial (LRF/LC 101 e núcleo: 1.1, 1.2, 4.1, 5.1, 7.1, 8.1, 15.1) · 2 obrigatório · 1 boa prática (blocos 17, 18, 21).
- Nível 1: média ponderada dentro de cada uma das 5 dimensões. Nível 2: média **simples** das dimensões.
- `nao_aplicavel` e `pendente` ficam fora. `nao_verificavel` define `score_min` (valendo 0) e `score_max` (valendo 1).
- `cobertura` = peso verificado / peso automatizado aplicável. Abaixo de 80% → "Avaliação incompleta" (fora do ranking).
- Classificação em faixas inspiradas no PNTP; Diamante/Ouro/Prata exigem todos os essenciais atendidos. **Conferir as faixas com a Cartilha PNTP 2026** antes de publicar.

No banco, `evaluation.cobertura` é gravada em % (0–100) e `scores_bloco` traz `dimensoes`, `blocos`, `contagem` por status e `essenciais_reprovados`.

## 8. Consultas úteis

```sql
-- ranking da última avaliação de cada ente
select e.slug, ev.score, ev.score_min, ev.score_max, ev.cobertura, ev.classificacao, ev.essenciais_ok
from monitor.v_latest_evaluation ev join monitor.entity e on e.id = ev.entity_id order by ev.score desc;

-- fila de revisão humana
select entity_slug, codigo, status, confianca, justificativa
from monitor.v_resultado_atual where confianca < 0.6 order by confianca;

-- busca semântica (Camada 3); :q é o vetor da consulta, com o mesmo modelo
select texto, 1 - (embedding <=> :q) as similaridade from monitor.chunk
where modelo = 'PORTULAN/serafim-100m-portuguese-pt-sentence-encoder-ir'
order by embedding <=> :q limit 5;
```

## 9. Testes

`tests/test_conformidade.py` (offline): checklist completo e aplicabilidade por poder, matemática do score e da faixa, monotonicidade da confiança, cada família de verificador, regras de `ausencia`, semântico com e sem embeddings, guarda de dimensão, pipeline ponta a ponta sobre o site falso, reuso de vetores entre rodadas, proteção de revisões e calibração. `tests/test_postgres_integration.py` roda a Camada 2 contra Postgres + pgvector no CI (v0.1 → v0.3, `vector(768)`, avaliação, evidências, reexecução sem duplicar).
