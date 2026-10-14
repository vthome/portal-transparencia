# Busca nos textos coletados: acentos, erros de digitação e busca semântica

A página **Busca** do dashboard (rota `GET /busca` da API) procura nos trechos da coleta mais recente de cada município. Este documento descreve os três níveis de busca, o que está em operação na PoC e o que depende de infraestrutura que a PoC não tem.

| Nível | Exemplo | Como funciona | Situação na PoC |
| --- | --- | --- | --- |
| Palavras, **sem acento e sem maiúsculas** | `orcamento` encontra "Orçamento" | Full-text em português do Postgres com `unaccent` antes do radical | ✅ Em operação |
| **Aproximada** (erro de digitação) | `orcameto` encontra "orçamento"; `conrato` encontra "contrato" | Similaridade de trigramas (`pg_trgm`), só quando a busca exata não acha nada | ✅ Em operação |
| **Semântica** (por significado) | "folha de pagamento" encontra "remuneração dos servidores" | Embeddings SERAFIM comparados no pgvector | ⏸️ Preparada, **não ativada** na versão publicada (ver Parte B) |

Importante: os embeddings do SERAFIM **são usados na PoC** — no motor de conformidade (verificador semântico, 8 quesitos), dentro do job de Conclusão no GitHub Actions. O que não está ativo é o uso deles na página Busca da versão publicada.

## Parte A — Busca por palavras (em operação)

### 1. Acentos e maiúsculas

**Problema encontrado:** a busca usava a configuração `portuguese` do Postgres, que mantém os acentos. "orçamento" e "orcamento" viravam termos diferentes: com cedilha, a busca devolvia dezenas de trechos; sem cedilha, nenhum.

**Solução (schema v0.5, seção 6.4 do `sql/schema.sql`):**

- extensão `unaccent` e a configuração de texto `monitor.pt_sem_acento`, cópia da `portuguese` que **tira os acentos antes de reduzir a palavra ao radical**. "orçamento", "Orçamento", "ORCAMENTO" e "orcamento" viram o mesmo termo (`orcament`);
- índice GIN `ix_chunk_fts_sem_acento` sobre `to_tsvector('monitor.pt_sem_acento', texto)`, que substitui o `ix_chunk_fts` da v0.4 (apagado para não ocupar espaço no plano gratuito);
- `Repo.busca_texto` (`api/database.py`) usa a nova configuração. Se o banco ainda estiver na v0.4, volta para a busca antiga em vez de falhar e registra um aviso no log.

### 2. Erros de digitação (busca aproximada)

Quando a busca exata não acha nada, a API tenta a **busca aproximada** antes de responder "nada encontrado":

- a consulta e cada trecho passam pela função `monitor.sem_acento` (minúsculas, sem acento);
- `word_similarity` do `pg_trgm` compara os grupos de 3 letras da consulta com o trecho e aceita a partir de **0,5** (`Repo.LIMIAR_APROXIMADA`);
- a resposta vem com `modo: "aproximada"`, ordenada pela semelhança, e o dashboard avisa que está mostrando os trechos mais parecidos.

| Digitado | Encontra | Semelhança aproximada |
| --- | --- | --- |
| orcameto | orçamento | 0,78 |
| conrato | contrato | 0,75 |
| ouvidorai | ouvidoria | 0,70 |
| licitassao | licitação | 0,64 |
| despeza | despesa | 0,62 |

Valores estimados com uma reprodução simplificada do cálculo; o Postgres pode dar números um pouco diferentes.

**Limites:** não encontra sinônimos (isso é papel da busca semântica); em palavras de 3 ou 4 letras uma letra errada já derruba a semelhança abaixo do limite; erros grandes (duas ou mais trocas numa palavra média) ficam de fora; se o erro forma outra palavra que existe nos textos, a busca exata responde e a aproximada não é acionada. A busca aproximada percorre os trechos da rodada mais recente sem índice próprio — suficiente para 7 municípios; com dezenas de entes, convém um índice de trigramas.

### 3. Testes

`tests/test_api_postgres.py::test_busca_sem_acento_e_aproximada` (Postgres real, no CI): "licitacoes", "LICITAÇÕES" e "Licitações" encontram os mesmos trechos; "remuneracap" cai na busca aproximada e encontra "Remuneração". `tests/test_api.py` e `tests/test_dashboard.py` cobrem a troca de modo e o aviso na página.

## Parte B — Busca semântica: restrição do modelo e situação na PoC

### Por que não está ativa na versão publicada

Para buscar por significado, **a consulta digitada precisa virar um vetor com o mesmo modelo que gerou os vetores dos trechos** (SERAFIM, 768 dimensões). Os vetores dos trechos já estão no banco, gerados pela Conclusão no GitHub Actions; falta só vetorizar a consulta, no momento da busca.

| Recurso | Necessário | Disponível na PoC |
| --- | --- | --- |
| Memória para carregar o SERAFIM (PyTorch + modelo) | ~1,5 GB | 512 MB (Render gratuito, onde roda a API) |

Carregar o modelo dentro da API (`API_BUSCA=modelo`) faria o Render reiniciar o serviço por falta de memória. Por isso, a API publicada roda com `API_BUSCA=texto`: busca por palavras, sem acento e tolerante a erros de digitação (Parte A). Localmente, com memória de sobra, `API_BUSCA=modelo` já faz a busca semântica — é a forma de demonstrá-la.

### Solução proposta: serviço externo de inferência

A saída que mantém a API no plano gratuito é **tirar dela só a vetorização da consulta**:

```
Dashboard ──► API (Render free) ──POST /embed──► Serviço de embeddings (SERAFIM, ~2 GB)
                  │                                   devolve o vetor da consulta
                  └──► Supabase: pgvector compara com os vetores dos trechos
```

- A API continua leve: faz uma chamada HTTP e a comparação no banco.
- O serviço só vetoriza a consulta; não acessa o banco nem guarda nada.
- Se o serviço estiver fora do ar, dormindo ou lento, a busca **cai para a busca por palavras** e o dashboard mostra um aviso. Nada quebra.
- A API confere se o serviço usa o mesmo modelo dos trechos (`EMBEDDINGS_MODELO`); vetores de modelos diferentes não são comparáveis.

**Situação: não ativada na PoC.** O serviço precisa de um host com cerca de 2 GB de memória, como o **Google Cloud Run**, que tem cota gratuita mas exige conta de faturamento (cartão), ou uma instância paga. Os Spaces Docker do Hugging Face passaram a exigir plano pago. Como a PoC foi mantida inteiramente em serviços gratuitos sem cartão, o serviço não foi publicado.

O **código está preparado** no repositório, para ativar quando houver o host: `servicos/embeddings/` (serviço FastAPI + Dockerfile), `api/embedder_remoto.py` (cliente) e o modo `API_BUSCA=remoto`, com testes em `tests/test_busca_remota.py`. Sem a URL e o token do serviço configurados, a API segue na busca por palavras. A Parte C descreve como ativar.

Outras formas de ligar a busca semântica, também não adotadas na PoC: instância da API com 2 GB (paga, sem serviço separado); versão do SERAFIM comprimida em ONNX 8 bits dentro da API gratuita (perto do limite de 512 MB e com vetores apenas aproximados aos gravados — exigiria medição antes).

## Parte C — Como ativar a busca semântica (quando houver o host)

### 4. Onde hospedar o serviço

O serviço é um contêiner (`servicos/embeddings/Dockerfile`) que precisa de cerca de **2 GB de memória**. Opções, conferidas em outubro de 2026 (os planos mudam; confira antes):

| Host | Custo | Observação |
| --- | --- | --- |
| **Google Cloud Run** (recomendado) | Gratuito dentro da cota mensal (180 mil vCPU-segundos, 360 mil GiB-segundos e 2 milhões de requisições) | Escala para zero: sem uso, não cobra. Exige uma conta de faturamento (cartão), mesmo dentro da cota. A primeira consulta depois de um tempo parado leva alguns segundos a mais |
| Render, instância paga com 2 GB | Pago | Mesmo painel da API; não dorme |
| Hugging Face Space (Docker) | Exige plano PRO | Spaces Docker deixaram de ser criados em contas gratuitas |

O passo a passo abaixo usa o **Cloud Run**. Em qualquer outro host, basta rodar o mesmo contêiner com as variáveis do item 5.

### 5. Variáveis

| Onde | Variável | Valor |
| --- | --- | --- |
| Serviço | `EMBEDDINGS_TOKEN` | Um segredo longo. Gere com `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| Serviço | `EMBEDDINGS_MODELO` | Já vem no Dockerfile (SERAFIM). Só mude se mudar também na Conclusão |
| API (Render → Environment) | `API_BUSCA` | `remoto` |
| API | `EMBEDDINGS_REMOTO_URL` | Endereço do serviço, ex.: `https://embeddings-serafim-xxxx.a.run.app` |
| API | `EMBEDDINGS_REMOTO_TOKEN` | O mesmo valor de `EMBEDDINGS_TOKEN` |
| API (opcional) | `EMBEDDINGS_REMOTO_TIMEOUT_S` | Espera máxima pelo serviço, em segundos (padrão 20) |
| GitHub → Actions → Variables (opcional) | `EMBEDDINGS_REMOTO_URL` | O workflow *Saúde da API* passa a "acordar" o serviço nos dias úteis de manhã |

### 6. Publicar no Cloud Run

Pré-requisitos: um projeto no Google Cloud com faturamento ativo e o [gcloud](https://cloud.google.com/sdk/docs/install) instalado e logado (`gcloud auth login`).

```powershell
gcloud config set project SEU_PROJETO
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com

gcloud run deploy embeddings-serafim `
  --source servicos/embeddings `
  --region us-east1 `
  --memory 2Gi --cpu 1 `
  --min-instances 0 --max-instances 1 --concurrency 4 `
  --cpu-boost `
  --timeout 60 `
  --allow-unauthenticated `
  --set-env-vars EMBEDDINGS_TOKEN=COLE_O_TOKEN
```

- `--source` faz o build do Dockerfile na nuvem (o modelo é baixado nesse build e fica dentro da imagem). O primeiro build leva alguns minutos.
- `--min-instances 0` deixa o serviço dormir sem uso (custo zero). `--max-instances 1` impede que um pico de chamadas gere custo.
- `--cpu-boost` acelera a partida (carregar o modelo).
- `--allow-unauthenticated` deixa a URL acessível pela internet; quem protege é o `EMBEDDINGS_TOKEN`. Sem o token, `/embed` responde 401.
- `us-east1` fica perto da região do Render usada pela API (Virginia).
- Para não deixar o token visível no painel, use o Secret Manager: `--set-secrets EMBEDDINGS_TOKEN=embeddings-token:latest`.

No fim, o comando mostra a **Service URL**. Teste:

```powershell
Invoke-RestMethod https://SERVICE_URL/health
# status: ok · modelo: PORTULAN/serafim-100m-portuguese-pt-sentence-encoder-ir · dim: 768

$r = Invoke-RestMethod -Method Post -Uri https://SERVICE_URL/embed `
       -Headers @{ Authorization = "Bearer COLE_O_TOKEN" } -ContentType "application/json" `
       -Body '{"textos": ["orcamento anual"]}'
$r.modelo; $r.vetores[0].Count        # o modelo e 768
```

### 7. Ligar na API

No Render → serviço `monitor-transparencia-api` → **Environment**: `API_BUSCA=remoto`, `EMBEDDINGS_REMOTO_URL` e `EMBEDDINGS_REMOTO_TOKEN`. O Render reinicia a API sozinho.

Conferir:

- `GET /health` mostra `"busca": "semantica_remota"`; a página **Monitoramento** mostra "Semântica (serviço externo)".
- Na página **Busca**, procurar "folha de pagamento": a legenda deve dizer **busca semântica (por significado)**.
- Se aparecer o aviso "A busca por significado está indisponível agora", o motivo vem no próprio aviso (token recusado, modelo diferente, tempo esgotado…) e nos **Logs** do Render.

### 8. Comportamento e limites

| Situação | O que acontece |
| --- | --- |
| Serviço no ar | Busca semântica (pgvector), `modo=semantica` |
| Serviço dormindo e demora mais que o timeout | Busca por palavras com aviso; a consulta seguinte já encontra o serviço acordado |
| Serviço fora do ar, token errado ou modelo diferente | Busca por palavras com aviso explicando o motivo |
| `GET /busca?modo=semantica` com o serviço indisponível | 503 com o motivo (quem pediu explicitamente a semântica não recebe outra busca) |
| `API_BUSCA=remoto` sem URL ou token | Busca por palavras; aviso no log da API |

Limites do serviço por chamada: até 16 textos e 2.000 caracteres por texto (`EMBEDDINGS_MAX_TEXTOS`, `EMBEDDINGS_MAX_CARACTERES`). A API envia só a consulta (até 300 caracteres).

**Mesmo modelo, mesmos vetores.** O serviço chama o SERAFIM do mesmo jeito que a Conclusão (`normalize_embeddings=True`). Se um dia `SentenceTransformerEmbedder.encode` mudar em `ingestion/embeddings/generate_embeddings.py`, mude também `SerafimLocal.encode` em `servicos/embeddings/app.py`.

**Custo.** Cada consulta usa uma fração de segundo de CPU; a partida a frio (carregar o modelo) é o que mais consome. Mesmo com uma partida a frio por consulta, a cota gratuita do Cloud Run comporta milhares de buscas por mês. Acompanhe em Cloud Run → serviço → **Metrics**.

### 9. Testes

`tests/test_busca_remota.py` (roda no CI, sem baixar o modelo):

- cliente: token no cabeçalho, timeout, erro de rede, 401, 500, resposta que não é JSON, modelo diferente, dimensão errada;
- rota `/busca`: semântica pelo serviço; queda para palavras (e para a aproximada) com aviso; 503 quando a semântica foi pedida explicitamente; `/health` informa `semantica_remota`;
- serviço: exige token na partida e em `/embed`, limita quantidade e tamanho dos textos, `/health`;
- contrato ponta a ponta: o cliente da API conversando com o serviço de verdade (com um modelo falso).
