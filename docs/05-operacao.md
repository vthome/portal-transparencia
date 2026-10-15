# Operação — Agendamento e CI (GitHub Actions)

Todo o ciclo roda de graça no GitHub Actions. Em repositório público, os minutos são ilimitados. Em repositório privado, a franquia free é de 2.000 minutos por mês: com a coleta diária (coleta + conclusão ≈ 30–60 min por dia), o uso fica perto do limite. Para repositório privado, prefira o repositório público ou acompanhe em Settings → Billing.

| Workflow | Arquivo | Quando roda | O que faz |
|---|---|---|---|
| **CI** | `ci.yml` | a cada push e pull request, e manual | Sintaxe, formatos YAML/TOML/JSON e lint. Depois, todos os testes (unitários, Postgres + pgvector efêmero, API e dashboard), com resumo das falhas na página do job |
| **Coleta agendada** | `coleta.yml` | **todos os dias às 11:00** (Brasília), e manual | Camada 1: preflight, coleta e extração dos 7 municípios, gravando no Supabase |
| **Conclusão** | `conclusao.yml` | **logo após cada coleta bem-sucedida**, e manual | Camada 2: embeddings (SERAFIM 768), conformidade e score da rodada nova |
| **Saúde da API** | `saude.yml` | **dias úteis** ~07:37 (Brasília), e manual | Teste de fumaça da API publicada no Render. Também acorda a API do plano free antes do expediente |

```
diário 11:00   ─► Coleta ─(sucesso)─► Conclusão ─► Supabase ─► API (Render) ─► Dashboard (Streamlit)
dias úteis 07:37 ─► Saúde da API  (falhou? o GitHub avisa por e-mail)
cada push ───────► CI (lint + testes)
```

## Configuração (uma vez)

GitHub → repositório → **Settings → Secrets and variables → Actions**.

| Tipo | Nome | Usado por | Valor |
|---|---|---|---|
| Secret | `DATABASE_URL` | Coleta, Conclusão | a do `.env` (usuário **postgres**, que grava) |
| Secret | `SUPABASE_URL` | Coleta | a do `.env` |
| Secret | `SUPABASE_SECRET_KEY` | Coleta | a do `.env` (`sb_secret_...`) |
| Variable | `CRAWLER_USER_AGENT` | Coleta | `MonitorTransparenciaBot/0.1 (+https://github.com/<usuario>/portal-transparencia; <email>)` |
| Variable | `API_URL` | Saúde da API | `https://monitor-transparencia-api.onrender.com` |
| Variable (opcional) | `EMBEDDINGS_MODELO`, `EMBEDDINGS_DIM` | Conclusão | só se trocar o modelo (padrão: SERAFIM, 768) |

O CI não precisa de nenhum secret: ele usa um Postgres descartável criado dentro do próprio job e **nunca** acessa o Supabase.

## Rodar manualmente

**Actions → (workflow) → Run workflow**:
- **Coleta agendada:** dá para informar `entes` (ex.: `rj-marica,rj-niteroi`) e `max_paginas`. Ao terminar, a Conclusão dispara sozinha.
- **Conclusão:** reavalia uma rodada sem recoletar. Os campos são:
  - `run`: o número da rodada; vazio usa a mais recente.
  - `entes`: os municípios a reavaliar; vazio reavalia todos.
  - `embedder`: `modelo` usa o SERAFIM; `hash` é só para teste.
  - `forcar`: substitui avaliações que já têm revisão humana. As revisões da rodada são apagadas.
- **Saúde da API:** roda o teste de fumaça na hora.
- **CI:** roda os testes sem precisar de push.

Cada job deixa o relatório no **resumo** (a página da execução) e como **artefato** para download: `resumo-coleta` na Coleta, `relatorio-conformidade` na Conclusão.

## O que muda conforme o resultado

- **Coleta parcial** (algum município falhou ou ficou bloqueado): a execução termina com sucesso e a Conclusão roda. Os quesitos afetados ficam `nao_verificavel` e alargam a faixa de incerteza; não contam como descumprimento.
- **Coleta que falha inteira** (banco fora, preflight reprovado): a Conclusão **não** roda, para não reavaliar a rodada anterior. O GitHub avisa por e-mail.
- **Saúde da API falhou:** abra o resumo do job. Ele mostra qual rota falhou. Depois veja os **Logs** do serviço no Render.

## Limites do GitHub para saber

- O cron é em **UTC** e pode atrasar de minutos a cerca de 1 hora em horários de pico. A coleta está marcada para 14:00 UTC = 11:00 em Brasília.
- **Espaço no Supabase (plano free: 500 MB de banco):** cada rodada guarda o texto das páginas coletadas. Com coleta diária, acompanhe em Supabase → Database → **Database size**; os trechos de embeddings já são podados (ficam só as 2 últimas rodadas).
- Em repositório **sem nenhum commit por 60 dias**, o GitHub **desliga os agendamentos** e avisa por e-mail. Para religar: Actions → workflow → **Enable workflow**.
- A Conclusão só é disparada automaticamente pela Coleta que roda no **branch padrão** (`main`).
- Limite de tempo por job: Coleta 90 min, Conclusão 60 min, CI 25 min.

## Checklist antes de cada push

```powershell
python scripts\verificar.py
```

Ele confere se os arquivos estão completos e atualizados e roda sintaxe, formatos, lint e testes. Se terminar em `TUDO OK`, o CI vai passar.

## Badges no README (opcional)

Troque `<usuario>` pelo seu usuário do GitHub:

```markdown
![CI](https://github.com/<usuario>/portal-transparencia/actions/workflows/ci.yml/badge.svg)
![Coleta](https://github.com/<usuario>/portal-transparencia/actions/workflows/coleta.yml/badge.svg)
![Saúde da API](https://github.com/<usuario>/portal-transparencia/actions/workflows/saude.yml/badge.svg)
```
