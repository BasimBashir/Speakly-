"""HTTP server for XTTS-v2-Urdu-FT (voice-cloned male Urdu TTS).

Loads the fine-tuned model once, precomputes speaker conditioning latents from a
baked male reference (reference.wav), and exposes:

    POST /tts   {"text": "<urdu text>"}  -> audio/wav (24 kHz mono)
    GET  /health

The whole utterance is synthesized then returned as one WAV; the pipecat client
streams audio frames from it. Long text is split into sentences (the model
degrades on very long inputs) and concatenated.
"""

import glob
import io
import os
import re

import numpy as np
import soundfile as sf
import torch
from fastapi import FastAPI
from fastapi.responses import Response
from pydantic import BaseModel
from TTS.tts.configs.xtts_config import XttsConfig
from TTS.tts.models.xtts import Xtts

LANG = "ur"
REFERENCE = "/app/reference.wav"
SAMPLE_RATE = 24000
# Conservative sampling (per the model card) for stable, clear Urdu.
TEMPERATURE = 0.3
TOP_K = 30
TOP_P = 0.85

app = FastAPI(title="xtts-urdu")
_model = None
_gpt_latent = None
_speaker_emb = None


def _load():
    global _model, _gpt_latent, _speaker_emb
    if _model is not None:
        return
    snap = glob.glob(
        "/root/.cache/huggingface/hub/models--suhaibrashid17--XTTS-v2-Urdu-FT/snapshots/*"
    )[0]
    cfg = XttsConfig()
    cfg.load_json(os.path.join(snap, "config.json"))
    model = Xtts.init_from_config(cfg)
    model.load_checkpoint(
        cfg,
        checkpoint_path=os.path.join(snap, "model.pth"),
        vocab_path=os.path.join(snap, "vocab.json"),
        use_deepspeed=False,
    )
    model.to("cuda" if torch.cuda.is_available() else "cpu")
    model.eval()
    gpt_latent, speaker_emb = model.get_conditioning_latents(
        audio_path=[REFERENCE],
        gpt_cond_len=cfg.gpt_cond_len,
        max_ref_length=cfg.max_ref_len,
        sound_norm_refs=cfg.sound_norm_refs,
    )
    _model, _gpt_latent, _speaker_emb = model, gpt_latent, speaker_emb


def _split_sentences(text: str) -> list[str]:
    # Split on Urdu/Latin sentence terminators; keep it simple + robust.
    parts = re.split(r"(?<=[۔?!.])\s+", text.strip())
    return [p for p in (s.strip() for s in parts) if p]


class TTSRequest(BaseModel):
    text: str


@app.on_event("startup")
def _startup():
    _load()


@app.get("/health")
def health():
    return {"status": "ok", "loaded": _model is not None}


@app.post("/tts")
def tts(req: TTSRequest):
    _load()
    chunks = []
    for sentence in _split_sentences(req.text) or [req.text]:
        out = _model.inference(
            sentence,
            LANG,
            _gpt_latent,
            _speaker_emb,
            temperature=TEMPERATURE,
            top_k=TOP_K,
            top_p=TOP_P,
            enable_text_splitting=False,
        )
        wav = np.asarray(out["wav"], dtype="float32")
        chunks.append(wav)
    audio = np.concatenate(chunks) if chunks else np.zeros(1, dtype="float32")
    buf = io.BytesIO()
    sf.write(buf, audio, SAMPLE_RATE, format="WAV", subtype="PCM_16")
    return Response(content=buf.getvalue(), media_type="audio/wav")
