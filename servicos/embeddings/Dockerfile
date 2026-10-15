# Serviço de embeddings (SERAFIM) para a busca semântica da API.
# Build a partir DESTA pasta:  gcloud run deploy ... --source servicos/embeddings
# O modelo é baixado no build e fica dentro da imagem: a partida não depende do Hugging Face
# e o "acordar" do serviço leva só o tempo de carregar o modelo na memória.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    HF_HOME=/opt/modelos HF_HUB_DISABLE_TELEMETRY=1 TOKENIZERS_PARALLELISM=false \
    EMBEDDINGS_MODELO=PORTULAN/serafim-100m-portuguese-pt-sentence-encoder-ir

WORKDIR /app
# PyTorch só CPU (bem menor que o padrão com CUDA)
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# baixa o modelo durante o build (camada própria: só refaz se o modelo mudar)
RUN python -c "import os; from sentence_transformers import SentenceTransformer; SentenceTransformer(os.environ['EMBEDDINGS_MODELO'], device='cpu')"

COPY app.py .
RUN useradd --create-home --uid 1000 servico && chown -R servico /app /opt/modelos
USER servico
ENV HF_HUB_OFFLINE=1

# Cloud Run (e a maioria dos hosts) informa a porta em $PORT
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1"]
