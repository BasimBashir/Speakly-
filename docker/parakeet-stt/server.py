"""Minimal OpenAI-compatible ASR server for NVIDIA Parakeet TDT 0.6B v3.

Exposes ``POST /v1/audio/transcriptions`` (OpenAI shape: multipart ``file`` +
``model`` + ``language``) so the backend reuses its existing OpenAI-compatible
STT client. Parakeet v3 covers 25 European languages (EN/ES/DE/FR/IT/PT/NL, ...)
and detects the language itself, so ``language`` is accepted but ignored.
Runs the ONNX export via onnx-asr on the GPU (falls back to CPU).
"""

import io
import os

import numpy as np
import onnx_asr
import onnxruntime as ort
import soundfile as sf
from fastapi import FastAPI, File, Form, UploadFile
from scipy.signal import resample_poly

MODEL_ID = os.getenv("PARAKEET_MODEL", "nemo-parakeet-tdt-0.6b-v3")

ort.preload_dlls()  # load the pip-installed CUDA/cuDNN libraries
app = FastAPI(title="parakeet-stt")
# The 24 GB card is shared with vLLM/Kokoro/TEI. ORT's default arena doubles on
# growth, which under concurrent calls pushed VRAM to the limit and stalled every
# GPU service (WDDM spills to system RAM). Grow only as requested, capped.
_CUDA = {
    "arena_extend_strategy": "kSameAsRequested",
    "gpu_mem_limit": int(float(os.getenv("PARAKEET_GPU_MEM_GB", "3.5")) * 1024**3),
    "cudnn_conv_algo_search": "HEURISTIC",
}
_model = onnx_asr.load_model(
    MODEL_ID,
    "/models/parakeet",
    providers=[("CUDAExecutionProvider", _CUDA), "CPUExecutionProvider"],
)
# Warm-up at the longest expected utterance so the arena is sized up front: a
# GPU allocation mid-call (new, longer audio) stalled vLLM for ~2.7 s on WSL2
# whenever STT and the LLM overlapped. shortcut: utterances > 30 s still grow it.
_rng = np.random.default_rng(0)
for _secs in (1, 30):
    _model.recognize((_rng.standard_normal(16000 * _secs) * 0.01).astype(np.float32))


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/v1/audio/transcriptions")
def transcribe(
    file: UploadFile = File(...),
    model: str = Form(default=MODEL_ID),
    language: str = Form(default=""),
    response_format: str = Form(default="json"),
):
    audio, sr = sf.read(io.BytesIO(file.file.read()), dtype="float32")
    if audio.ndim == 2:  # (samples, channels) -> mono
        audio = audio.mean(axis=1)
    if sr != 16000:  # phone audio arrives at 8 kHz; Parakeet expects 16 kHz
        audio = resample_poly(audio, 16000, sr).astype(np.float32)
    text = _model.recognize(audio) if audio.size else ""
    return {"text": (text or "").strip()}
