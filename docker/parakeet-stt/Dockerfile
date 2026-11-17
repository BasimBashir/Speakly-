# NVIDIA Parakeet TDT 0.6B v3 served OpenAI-compatibly: fast multilingual
# (25 European languages) STT. Additive optional Transcriber provider.
# GPU via onnxruntime-gpu (CUDA/cuDNN come as pip wheels); on CPU it was slower
# than Whisper-turbo on GPU, so CPU-only has no point here.
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
        "onnx-asr[hub]" "onnxruntime-gpu[cuda,cudnn]" scipy soundfile \
        fastapi "uvicorn[standard]" python-multipart

WORKDIR /app

# Bake the (ungated) ONNX weights into the image so start-up needs no network.
# Plain directory, not the HF cache: onnxruntime rejects the cache's symlinked
# external-data blobs ("External data path escapes model directory").
RUN python -c "from huggingface_hub import snapshot_download; snapshot_download('istupakov/parakeet-tdt-0.6b-v3-onnx', local_dir='/models/parakeet')"

# Copied last so server edits don't invalidate the cached model-download layer.
COPY server.py /app/server.py

EXPOSE 8000
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
