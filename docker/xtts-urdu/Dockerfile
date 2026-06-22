# XTTS-v2 fine-tuned for Urdu (suhaibrashid17/XTTS-v2-Urdu-FT), served over HTTP.
# Cleaner/more natural Urdu voice than Piper; voice-cloned from a baked male
# reference (reference.wav). Additive optional TTS provider for Urdu calls;
# Piper remains the lightweight fallback.
#
# Coqui TTS is unmaintained, so EVERY version here is pinned to the exact stack
# proven to load + synthesize Urdu (validated 2026-06-22):
#   torch 2.5.1 (<2.9 so no torchcodec needed) + transformers 4.57.6 (has both
#   isin_mps_friendly and is_torchcodec_available) + spacy (Arabic tokenizer) +
#   coqui-tts 0.27.5 + the repo's custom tokenizer.py.
# GPU: XTTS needs CUDA for usable latency (VRAM ~2 GB).
FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg git libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# CUDA torch 2.5.1 (cu121 wheels bundle the CUDA runtime).
RUN pip install --no-cache-dir \
        torch==2.5.1 torchaudio==2.5.1 --index-url https://download.pytorch.org/whl/cu121
RUN pip install --no-cache-dir \
        "transformers==4.57.6" "coqui-tts==0.27.5" spacy \
        fastapi "uvicorn[standard]" soundfile huggingface_hub

WORKDIR /app

# Download the public (MIT) fine-tuned model + patch the custom Urdu tokenizer
# over coqui's tokenizer.py so Urdu script is handled natively.
RUN python - <<'PY'
import glob, os, shutil
from huggingface_hub import snapshot_download
snap = snapshot_download("suhaibrashid17/XTTS-v2-Urdu-FT")
import TTS
xdir = os.path.join(os.path.dirname(TTS.__file__), "tts/layers/xtts")
for name in ("tokenizer.py", "tokenizers.py"):
    dst = os.path.join(xdir, name)
    if os.path.exists(dst):
        shutil.copy(os.path.join(snap, "tokenizer.py"), dst)
print("model + tokenizer ready at", snap)
PY

# Baked male native-accent reference (Piper ur_PK-fasih). Conditioning latents
# are precomputed from this at startup, so every agent gets the same voice.
COPY reference.wav /app/reference.wav
COPY server.py /app/server.py

EXPOSE 8000
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
