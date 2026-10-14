# Backlog — ajustes para próximas versões

## Concluído

- `SUPABASE_SERVICE_KEY` → `SUPABASE_SECRET_KEY` e `COLETA_USER_AGENT` → `CRAWLER_USER_AGENT`.
- Crawler: portal descoberto expandido, canais externos não expandidos, orçamento reservado ao portal, links de onclick/data-href/select, renderização por menu JS, downloads sem extensão, relatório por host.
- Camada 2 (conclusão): schema v0.3 (`vector(768)`), embeddings SERAFIM 768 com reuso e gravação retomável, 142 quesitos / 13 verificadores, confiança calibrável, score em dois níveis com faixa min–max. Ver `claude/02-conclusao.md`.
- Camada 3 (API): FastAPI com 20 rotas, `render.yaml`, usuário restrito `sql/api_role.sql`, schema v0.4. Publicada no Render. Ver `claude/03-api.md`.
- Camada 3 (dashboard): Streamlit com 6 páginas, só via API; testado com AppTest. Publicado no Streamlit Cloud. Ver `claude/04-dashboard.md`.
- Agendamento e CI (GitHub Actions): CI a cada push (qualidade + testes com Postgres/pgvector efêmero), coleta diária às 07:00, conclusão após cada coleta bem-sucedida, teste de saúde da API nos dias úteis; `scripts/verificar.py` antes do push. Ver `claude/05-operacao.md`.
- Busca nos textos (schema v0.5, 14/10/2026): sem acento e sem maiúsculas (`unaccent` + configuração `monitor.pt_sem_acento`; "orcamento" encontra "Orçamento") e busca aproximada por trigramas quando a exata não acha nada (erros de digitação, `modo=aproximada`). Ver `claude/06-busca.md`.

## Pendente

- **Aplicar o schema v0.5 no Supabase** (rodar `sql/schema.sql` e `sql/api_role.sql` de novo) e fazer o push da versão com a busca sem acento.
- **Busca semântica na versão publicada:** a consulta precisa ser vetorizada com o SERAFIM (~1,5 GB de RAM) e o Render gratuito tem 512 MB. Código do serviço externo de inferência preparado (`servicos/embeddings`, `API_BUSCA=remoto`), **não ativado**: exige host com ~2 GB (ex.: Cloud Run, com conta de faturamento). Alternativas: instância paga da API; SERAFIM em ONNX 8 bits dentro da API (medir memória e semelhança dos vetores). Ver `claude/06-busca.md`.
- Revisão humana: revisar ≥ 30 resultados na página Revisão humana e rodar `python -m compliance.calibrar`.
- Variável `API_URL` no GitHub (Actions → Variables) para o workflow de saúde.
- Rotas-semente do GeoSiap em `platforms.yaml` (Japeri).
- Confirmar o `*` no final da URL do Betha de Cachoeiras de Macacu e se `prefeituracachoeiras.com` é o domínio oficial.
- Conferir as faixas de classificação de `scoring.yaml` com a Cartilha PNTP 2026.
- Calibrar `semantico.alto/medio` (scoring.yaml) com uma amostra revisada usando o SERAFIM.
- Quesito 16.5 (AccessMonitor) segue `pendente`.
- Busca aproximada sem índice próprio: suficiente para 7 entes; com dezenas de municípios, criar índice de trigramas.
- Revisões valem para a avaliação de uma rodada; avaliar herança para a rodada seguinte quando a evidência não mudar.
- Página `projecoes.py` (previsão orçamentária) fora do escopo do monitor de conformidade.
