# Camada 3 — Dashboard (Streamlit Cloud)

O dashboard é a interface para quem vai consultar e revisar as avaliações. Ele **só conversa com a API** (Render), nunca com o banco: os secrets do Streamlit têm apenas o endereço da API e o token de revisão. Nenhuma senha do Supabase vai para o Streamlit.

```
Navegador ──► Streamlit Cloud (dashboard/app.py) ──HTTP──► API no Render ──► Supabase
                      │  X-API-Key só no servidor do Streamlit
                      └─ POST /revisoes (formulário de revisão, protegido por senha)
```

## Páginas

| Página | Para quê | Rotas da API |
|---|---|---|
| **Visão geral** | Ranking dos 7 municípios com a faixa de incerteza; heatmap município × dimensão; quesitos por situação. O ranking pode ser ordenado pelo score geral ou por uma dimensão | `/indicadores/*` |
| **Município** | Score, score revisado, cobertura, classificação e essenciais; score por dimensão; os 142 quesitos com filtros (situação, dimensão, essenciais, para revisar); detalhe do quesito com justificativa, evidências, cópias guardadas (screenshot/PDF) e revisões | `/entes/{slug}`, `/resultados/{id}`, `/evidencias/{id}/arquivo` |
| **Evolução** | Score por rodada com a faixa; o que melhorou ou piorou entre duas rodadas; histórico de um quesito | `/historico`, `/diff`, `/timeline` |
| **Revisão humana** | Fila de baixa confiança; formulário para gravar o veredito correto; concordância revisor × motor | `/revisoes/*` |
| **Busca** | Busca nos textos coletados, com link para a página de origem | `/busca` |
| **Monitoramento** | Saúde da API e das rodadas de coleta (falhas, páginas, seções ausentes); checklist em vigor | `/health`, `/rodadas`, `/checklist` |

O município escolhido fica na URL (`?ente=rj-marica`), então dá para compartilhar o link direto de um município.

Na primeira consulta depois de ~15 minutos sem uso, a API do plano free do Render "acorda" e pode levar até 1 minuto. O dashboard mostra "Conectando à API…" e, se demorar demais, um botão **Tentar de novo**. As consultas ficam em cache por 5 minutos, e por 1 minuto na fila de revisão.

## Quem pode revisar

- **Sem `API_TOKEN_REVISAO`:** a página de revisão fica **somente leitura**.
- **Com o token e `REVISAO_SENHA`:** o revisor digita a senha uma vez por sessão para liberar o formulário. **Recomendado**, porque o app do Streamlit Cloud é público por padrão.
- **Com o token e sem senha:** qualquer visitante pode gravar revisões. Use só localmente.

A revisão grava nome, veredito e comentário. Ela não apaga o veredito do motor: só muda o **status final** e o **score revisado** (ver `docs/03-api.md`).

## Rodar localmente

```powershell
pip install -r dashboard\requirements.txt
.\scripts\dashboard.ps1 -Api https://monitor-transparencia-api.onrender.com   # usa a API do Render
# ou, com a API local rodando (.\scripts\api.ps1) em outra janela:
.\scripts\dashboard.ps1
```

Abre em http://localhost:8501. Para testar a revisão localmente, copie `.streamlit\secrets.toml.example` para `.streamlit\secrets.toml` e preencha os valores. Esse arquivo já está no `.gitignore`.

## Deploy no Streamlit Cloud

1. **Push** do repositório para o GitHub, com as pastas `dashboard/` e `.streamlit/` (sem o `secrets.toml`).
2. Em **https://share.streamlit.io**, entre com o GitHub e clique em **Create app → Deploy a public app from GitHub**.
3. Preencha:
   - **Repository:** o seu `portal-transparencia`.
   - **Branch:** `main`.
   - **Main file path:** `dashboard/app.py`.
   - **App URL:** escolha, por exemplo `monitor-transparencia-rj`.
4. Em **Advanced settings**:
   - **Python version:** 3.12.
   - **Secrets:** cole (valores reais):
     ```toml
     API_URL = "https://monitor-transparencia-api.onrender.com"
     API_TOKEN_REVISAO = "o mesmo valor do Render (Environment → API_TOKEN_REVISAO)"
     REVISAO_SENHA = "uma senha para os revisores"
     ```
5. **Deploy.** O Streamlit Cloud instala só o `dashboard/requirements.txt` (streamlit, plotly, pandas, requests), que fica ao lado do `app.py` e por isso tem prioridade sobre o `requirements.txt` da raiz.
6. Para mudar secrets depois: no app, **⋮ → Settings → Secrets**. O app reinicia sozinho.

A cada push no GitHub, o Streamlit Cloud atualiza o app. A API no Render não é redeployada por mudanças só em `dashboard/`.

## Testes

| Arquivo | O que cobre |
|---|---|
| `tests/test_dashboard.py` | Cliente da API (rotas, 401/404/422, API fora do ar), formatos pt-BR, gráficos e **as 6 páginas com o AppTest do Streamlit** contra a API em memória. Cobre filtros, troca de município pela URL, diff, senha de revisão, gravação de revisão, modo somente leitura, busca, banco sem avaliações e API fora do ar |
| `tests/test_api_postgres.py::test_dashboard_sobre_dados_reais` | As 6 páginas contra a API real com Postgres + pgvector e dados das Camadas 1 e 2 (CI) |

`tests/streamlit_shim.py` permite rodar o AppTest sem o `pyarrow`. Com o `pyarrow` instalado, que é o normal, ele não faz nada.

## Gráficos

- **Uma só cor de série** (azul), com a mesma cor mais clara para avaliações incompletas, que também levam "(incompleta)" no rótulo.
- **Rampa azul** para magnitude (heatmap 0–100, com o valor escrito em cada célula).
- **Cores de status** (verde, amarelo, vermelho, cinzas) sempre acompanhadas de símbolo e rótulo (✅ 🟡 ❌ ❔ ⏳), nunca só a cor.
- **Faixa de incerteza** como barra de erro no ranking e como área na evolução.
- Um único eixo por gráfico. Tooltips em português.
