# Portal Transparência RJ

Prova de conceito para avaliação automatizada da conformidade de Portais da Transparência de municípios do Estado do Rio de Janeiro.

## Objetivo

Desenvolver um pipeline de coleta, extração e análise de evidências públicas, permitindo avaliar critérios de transparência, calcular indicadores de conformidade e disponibilizar resultados auditáveis.

## Tecnologias

- Python
- FastAPI
- Streamlit
- PostgreSQL / Supabase
- pgvector
- Sentence Transformers
- GitHub Actions

## Componentes

- `config/`: configuração dos municípios, plataformas e critérios.
- `ingestion/`: descoberta, coleta, extração e transformação.
- `compliance/`: classificação de conformidade, confiança e pontuação.
- `api/`: disponibilização dos resultados por API REST.
- `dashboard/`: visualização e monitoramento.
- `sql/`: estrutura do banco de dados.
- `tests/`: testes automatizados.

## Status

Projeto em desenvolvimento — estrutura inicial criada.

## Dados

As análises utilizarão informações públicas dos portais oficiais dos municípios selecionados. As avaliações deverão preservar evidências, fontes e datas de coleta.