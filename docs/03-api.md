# Camada 3 — API (FastAPI no Render)

A API lê o schema `monitor` do Supabase e entrega ao dashboard (Streamlit) e a qualquer cliente:
avaliações, histórico, diff entre rodadas, evidências e busca. Ela também é o **único caminho de escrita da revisão humana**
(`POST /revisoes`). Assim, o dashboard nunca recebe credenciais do banco.

```
Streamlit Cloud ──HTTP──► FastAPI (Render) ──SQL──► Supabase (monitor.*)
     │  X-API-Key (revisão)        │  links assinados ──► Storage (bucket privado)
     └──────── POST /revisoes ─────┘
```

## 1. Antes do deploy (Supabase)

1. **SQL Editor:** rode o `sql/schema.sql` de novo. A v0.4 cria o índice da fila de revisão; a v0.5 deixa a busca por texto sem acento e tolerante a erros de digitação (extensão `unaccent`, configuração `monitor.pt_sem_acento`, função `monitor.sem_acento`). Nada é apagado, exceto o índice antigo `ix_chunk_fts`, substituído por `ix_chunk_fts_sem_acento`.
2. **(Recomendado) Usuário só para a API:** o `sql/api_role.sql` cria o usuário `monitor_api`, que **lê** tudo e só **grava** em `human_review`. Delete, update e escrita em outras tabelas são bloqueados (há teste para isso).
   - **A senha é você quem inventa**, porque o usuário está sendo criado agora. Para gerar uma: `python -c "import secrets; print(secrets.token_urlsafe(24))"`. Ela só usa letras, números, `-` e `_`, então não quebra a URL.
   - Cole o arquivo no SQL Editor e troque `TROQUE_ESTA_SENHA` **só lá**, na linha `senha text := ...`. **Não salve a senha no arquivo:** ele vai para o GitHub. A senha fica apenas no `DATABASE_URL` do Render e no seu gerenciador de senhas.
   - Com o texto de exemplo, o script se recusa a criar o usuário.
   - Rodar de novo (por exemplo, depois de recriar o schema) só reaplica as permissões e **não muda a senha**, então pode rodar o arquivo como está no repositório.
   - Para trocar a senha depois: `alter role monitor_api password '...';` e atualize o `DATABASE_URL` no Render.
3. **Connection string** em Supabase → **Connect** → **Session pooler** (porta 5432). Com o usuário restrito, o usuário no pooler leva o sufixo do projeto:
   ```
   postgresql://monitor_api.<PROJECT_REF>:<SENHA>@<HOST>:5432/postgres
   ```
   `<HOST>` é **exatamente** o da `DATABASE_URL` do `.env` (ex.: `aws-1-sa-east-1.pooler.supabase.com` — o número varia por projeto). Host errado dá `tenant/user ... not found`.
   Sem o `api_role.sql`, use a mesma `DATABASE_URL` da coleta (usuário `postgres`).

## 2. Rodar localmente

```powershell
pip install -r requirements-api.txt
.\scripts\api.ps1                 # http://127.0.0.1:8000/docs (Swagger)
.\scripts\api.ps1 -Testar         # sobe e roda o teste de fumaça
```

A API usa `DATABASE_URL` e `API_TOKEN_REVISAO` do `.env` (veja `.env.example`).

## 3. Deploy no Render

O repositório já traz `render.yaml` (Blueprint), `requirements-api.txt` (enxuto: sem Playwright, pandas nem PyTorch) e `.python-version` (3.12).

1. Faça commit e push para o GitHub.
2. Render → **New +** → **Blueprint** → escolha o repositório. O Render lê o `render.yaml` e cria o serviço `monitor-transparencia-api`.
3. Preencha os valores pedidos:
   - `DATABASE_URL`: obrigatório. É a string do item 1.3.
   - `SUPABASE_URL` e `SUPABASE_SECRET_KEY`: opcionais, mas sem eles `GET /evidencias/{id}/arquivo` responde 503. Servem para gerar links temporários para screenshots e PDFs do bucket privado. A chave fica só no Render, nunca no Streamlit.
4. **Apply.** O build leva poucos minutos. Ao final, o Render chama `GET /health` e só considera o deploy pronto com resposta 200.
5. Em **Environment**, copie o valor gerado de `API_TOKEN_REVISAO`. Ele vai para os *secrets* do Streamlit, e é com ele que o dashboard grava revisões.
6. Teste a API publicada:
   ```powershell
   .\scripts\api.ps1 -Url https://monitor-transparencia-api.onrender.com
   # com escrita (grava 1 revisão de teste):
   python scripts\testar_api.py --url https://... --token <API_TOKEN_REVISAO> --revisao
   ```

Se você já criou o serviço manualmente, use estes valores:

| Campo | Valor |
| --- | --- |
| Build Command | `pip install -r requirements-api.txt` |
| Start Command | `uvicorn api.main:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips "*"` |
| Health Check Path | `/health` |

As variáveis de ambiente são as mesmas do `render.yaml`.

**Plano free:** o serviço dorme após cerca de 15 minutos sem chamadas, e a primeira chamada depois disso leva cerca de 1 minuto. O teste de fumaça já espera esse tempo no `/health`. O dashboard deve mostrar "carregando" na primeira chamada.

**Deploy automático:** acontece a cada push em arquivos que afetam a API. Mudanças só em `docs/`, `tests/`, `dashboard/`, `scripts/` e `.github/` não redeployam (`buildFilter`).

## 4. Endpoints

Documentação interativa em `/docs` (Swagger) e esquema em `/openapi.json`.

| Grupo | Rota | Para quê |
| --- | --- | --- |
| entes | `GET /entes` | lista com a última avaliação e a última coleta |
| | `GET /entes/{slug}` | URLs vigentes, avaliação e **score revisado** |
| indicadores | `GET /indicadores/ranking[?dimensao=]` | posição; incompletas no fim, sem posição |
| | `GET /indicadores/dimensoes` | matriz ente × dimensão (heatmap) |
| | `GET /indicadores/status` | contagem de status por ente |
| avaliações | `GET /entes/{slug}/historico` | evolução do score por rodada |
| | `GET /entes/{slug}/resultados` | 142 quesitos; filtros `run_id`, `status`, `dimensao`, `bloco`, `essencial`, `precisa_revisao` |
| | `GET /entes/{slug}/diff[?de=&para=]` | o que melhorou, piorou ou mudou entre rodadas |
| | `GET /entes/{slug}/quesitos/{codigo}/timeline` | histórico de um quesito |
| | `GET /resultados/{id}` | justificativa, sinais de confiança, evidências, revisões |
| revisão humana | `GET /revisoes/fila` | confiança < 0,6 ou não verificável, ainda sem revisão |
| | `POST /revisoes` 🔑 | grava o veredito do revisor e devolve o score revisado |
| | `GET /revisoes`, `GET /revisoes/concordancia` | histórico e concordância com o motor (base da recalibração) |
| documentos | `GET /evidencias/{id}/arquivo` | link temporário (1 h) para screenshot, HTML ou arquivo |
| | `GET /busca?q=` | busca nos trechos da coleta mais recente |
| monitoramento | `GET /rodadas`, `GET /rodadas/{id}` | saúde das coletas |
| | `GET /checklist` | quesitos em vigor |
| sistema | `GET /health` | banco, última rodada, schema |

🔑 = exige o cabeçalho `X-API-Key: <API_TOKEN_REVISAO>`.

**Exemplo de revisão:**

```bash
curl -X POST https://.../revisoes -H "X-API-Key: $TOKEN" -H "Content-Type: application/json" \
  -d '{"resultado_id": 123, "status_corrigido": "atende", "revisor": "Ana (MPRJ)", "comentario": "..."}'
```

## 5. Regras importantes

- **A revisão não sobrescreve o motor.** O veredito do revisor fica em `human_review`, e as respostas trazem `status` (do motor), `status_revisado` e `status_final`. O `score` é o do motor. O `score_revisado` usa o mesmo cálculo da Camada 2 com os status finais (há teste garantindo que, sem revisões, os dois são idênticos). Um quesito `pendente` revisado (ex.: 16.5) passa a contar no score.
- **O diff e a timeline** comparam o status final de cada quesito.
- **Reavaliação com `--forcar`** apaga a avaliação da rodada e as revisões dela. A API não tem rota que faça isso.
- **Leitura aberta** por padrão, porque são dados públicos de transparência. Para fechar, defina `API_TOKEN_LEITURA`: as rotas passam a exigir `X-API-Key`, menos `/health`, que o Render usa.
- **Erros de banco** (conexão perdida, timeout de 15 s) viram 503 com mensagem curta. O detalhe vai só para o log do Render.

## 6. Busca

| `API_BUSCA` | Como funciona | Onde roda | Na PoC |
| --- | --- | --- | --- |
| `texto` (padrão) | full-text em português do Postgres (`websearch_to_tsquery`, índice GIN), **sem acento e sem diferenciar maiúsculas**; se não achar nada, busca **aproximada** por trigramas (`pg_trgm`), que tolera erros de digitação | qualquer plano, inclusive o free | ✅ em uso no Render |
| `remoto` | a consulta é vetorizada no serviço de embeddings (`servicos/embeddings`) e comparada no pgvector; se o serviço falhar, cai para `texto` com aviso | API no plano free; o serviço precisa de ~2 GB (ex.: Cloud Run) | ⏸️ preparado, não ativado (sem host para o serviço) |
| `modelo` | a API carrega o SERAFIM e busca por similaridade no pgvector (mesmos vetores da Camada 2) | ~1,5 GB de RAM: instância Standard ou maior, com `sentence-transformers` e `torch` (CPU) no build | só localmente (demonstração) |

A resposta informa o `modo` usado: `semantica`, `texto` ou `aproximada`, e traz `aviso` quando a busca semântica estava configurada mas indisponível. Com `modo=semantica` e o modelo desligado, a API responde 409.

**Por que a busca semântica não está ativa na versão publicada:** a consulta precisa ser vetorizada com o mesmo modelo dos trechos (SERAFIM), que exige ~1,5 GB de memória; o Render gratuito tem 512 MB. A solução proposta — um serviço externo de inferência só para vetorizar a consulta — está com o código pronto, mas não foi publicada porque exige um host com ~2 GB (ex.: Cloud Run, que pede conta de faturamento). Os embeddings continuam em uso no motor de conformidade (verificador semântico). Detalhes e passo a passo de ativação em [06-busca-semantica-remota.md](06-busca-semantica-remota.md).

**Acentos e erros de digitação (schema v0.5).** A configuração `monitor.pt_sem_acento` tira os acentos *antes* do radical em português, então `orçamento`, `Orçamento`, `ORCAMENTO` e `orcamento` viram o mesmo termo e encontram os mesmos trechos. Quando a busca exata não acha nada (ex.: `orcameto`), a API compara a consulta com cada trecho por similaridade de trigramas (`word_similarity`, limiar 0,5, ambos sem acento) e responde com `modo=aproximada`; o dashboard avisa que está mostrando os trechos mais parecidos. Se o banco ainda estiver na v0.4, a API continua funcionando com a busca antiga (com acento) e registra um aviso no log.

## 7. Testes

| Arquivo | O que cobre | Onde roda |
| --- | --- | --- |
| `tests/test_api.py` | regras de apresentação, autenticação, validação (422), 404/409/503, link assinado, OpenAPI; banco simulado | sempre (`pytest`) |
| `tests/test_api_postgres.py` | todas as rotas sobre dados reais das Camadas 1 e 2 em Postgres + pgvector: duas rodadas, diff, timeline, revisão e score revisado, busca semântica e por texto, usuário restrito | CI (`TEST_DATABASE_URL`) |
| `scripts/testar_api.py` | teste de fumaça da API publicada (só leitura; `--revisao` grava 1 revisão) | depois de cada deploy |

> `tests/test_api_postgres.py` apaga e recria o schema `monitor`. **Nunca** aponte o `TEST_DATABASE_URL` para o Supabase.
