# Backlog — ajustes para próximas versões

## Concluído

- `SUPABASE_SERVICE_KEY` → `SUPABASE_SECRET_KEY` (sem fallback para o nome antigo).
- `COLETA_USER_AGENT` → `CRAWLER_USER_AGENT` (settings, preflight, .env.example, workflow, docs). Renomear também a variável no GitHub (Settings → Variables).
- Crawler: portal descoberto expandido, canais externos não expandidos, orçamento reservado ao portal (`COLETA_FRACAO_PORTAL`), links de onclick/data-href/select, renderização por menu JS, downloads sem extensão, relatório por host.
- Camada 2 (conclusão): schema v0.3 (`vector(768)`), embeddings SERAFIM 768 com reuso por hash e gravação retomável, 142 quesitos / 13 verificadores, confiança calibrável, score em dois níveis com faixa min–max, workflow `conclusao.yml`. Ver `claude/02-conclusao.md`.
- Camada 3 (API): FastAPI com 20 rotas, `render.yaml`, usuário restrito `sql/api_role.sql`, schema v0.4. Publicada no Render (13/10/2026). Ver `claude/03-api.md`.
- Camada 3 (dashboard): Streamlit com 6 páginas (visão geral, município, evolução, revisão humana com senha, busca, monitoramento), só via API; testado com AppTest. Ver `claude/04-dashboard.md`.

## Pendente

- Deploy do dashboard no Streamlit Cloud (`dashboard/app.py`, secrets API_URL / API_TOKEN_REVISAO / REVISAO_SENHA).
- Rotas-semente do GeoSiap em `platforms.yaml` (anotar rotas de Despesas, Receitas, Licitações, Contratos e Relatórios Fiscais navegando no portal de Japeri).
- Confirmar o `*` no final da URL do Betha de Cachoeiras de Macacu.
- Confirmar se `prefeituracachoeiras.com` é o domínio oficial.
- Conferir as faixas de classificação de `scoring.yaml` com a Cartilha PNTP 2026.
- Calibrar `semantico.alto/medio` (scoring.yaml) com uma amostra revisada usando o SERAFIM.
- Revisar humanamente ≥ 30 resultados (página Revisão humana) e rodar `python -m compliance.calibrar`.
- Quesito 16.5 (AccessMonitor) segue `pendente` — integração com a ferramenta externa.
- Busca semântica na API exige instância com ~1,5 GB (hoje o padrão é busca por texto no plano free).
- Revisões valem para a avaliação de uma rodada; avaliar se devem ser herdadas pela rodada seguinte quando a evidência não mudar.
- Página `projecoes.py` (previsão orçamentária) da estrutura sugerida ficou fora: exigiria coletar séries de receita/despesa, que o monitor de conformidade não coleta.
