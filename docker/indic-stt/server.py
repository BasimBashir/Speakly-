"""Minimal OpenAI-compatible ASR server for AI4Bharat IndicConformer.

Exposes ``POST /v1/audio/transcriptions`` (OpenAI shape: multipart ``file`` +
``model`` + ``language``) so the backend can reuse its existing OpenAI-compatible
STT client. Loads IndicConformer via HuggingFace transformers (trust_remote_code)
— supports Urdu, Hindi, and 20 other Indian languages, native-script output.
CPU by default (no GPU/VRAM contention with the existing model stack).
"""

import io

import numpy as np
import soundfile as sf
import torch
import torchaudio
from fastapi import FastAPI, File, Form, UploadFile
from transformers import AutoModel

MODEL_ID = "ai4bharat/indic-conformer-600m-multilingual"
DEFAULT_LANGUAGE = "ur"

app = FastAPI(title="indic-stt")
_model = None


def _get_model():
    global _model
    if _model is None:
        _model = AutoModel.from_pretrained(MODEL_ID, trust_remote_code=True)
        _model.eval()
    return _model


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/v1/audio/transcriptions")
async def transcribe(
    file: UploadFile = File(...),
    model: str = Form(default=MODEL_ID),
    language: str = Form(default=DEFAULT_LANGUAGE),
    response_format: str = Form(default="json"),
):
    raw = await file.read()
    # Decode with soundfile (avoids torchaudio's torchcodec backend dependency).
    audio, sr = sf.read(io.BytesIO(raw), dtype="float32")
    wav = torch.from_numpy(np.asarray(audio))
    if wav.ndim == 2:  # (samples, channels) -> mono
        wav = wav.mean(dim=1)
    wav = wav.unsqueeze(0)  # (1, samples), which IndicConformer expects
    if sr != 16000:
        wav = torchaudio.transforms.Resample(sr, 16000)(wav)

    lang = (language or DEFAULT_LANGUAGE).lower()
    with torch.no_grad():
        text = _get_model()(wav, lang, "ctc")
    if isinstance(text, (list, tuple)):
        text = " ".join(str(t) for t in text)
    return {"text": (text or "").strip()}
