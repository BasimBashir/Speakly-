# IndicConformer ASR served OpenAI-compatibly for self-hosted Urdu/Hindi STT.
# Additive: a new optional Transcriber provider; does not touch Speaches/Whisper.
# CPU-only (no GPU reservation) to avoid VRAM contention with vLLM/TEI/Kokoro.
FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# CPU torch wheels (much smaller than the CUDA build).
RUN pip install --no-cache-dir \
        torch torchaudio --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir \
        transformers "onnxruntime==1.20.1" "onnx==1.20.1" \
        fastapi "uvicorn[standard]" soundfile python-multipart

WORKDIR /app

# Pre-download model weights + custom code at build so the container is
# self-contained and the first request is fast. The model is a GATED HF repo,
# so the token is supplied as a BuildKit secret (never written to image layers):
#   docker build --secret id=hf_token,env=HF_TOKEN ...
# The downloaded weights are cached in the image; no token is needed at runtime.
RUN --mount=type=secret,id=hf_token \
    HF_TOKEN="$(cat /run/secrets/hf_token)" \
    HUGGING_FACE_HUB_TOKEN="$(cat /run/secrets/hf_token)" \
    python -c "from transformers import AutoModel; AutoModel.from_pretrained('ai4bharat/indic-conformer-600m-multilingual', trust_remote_code=True)"

# Copied last so server edits don't invalidate the cached model-download layer.
COPY server.py /app/server.py

EXPOSE 8000
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
