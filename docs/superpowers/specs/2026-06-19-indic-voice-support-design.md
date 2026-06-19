# Self-Hosted Indic Voice Support (Urdu / Hindi / Roman) — Design

**Date:** 2026-06-19
**Status:** Draft for review
**Fork:** Speakly (BasimBashir/Speakly-)
**Depends on:** `speakly-v1.35.0` (must be pushed/promoted before building)

## Problem & goal

Add Urdu / Hindi / **Roman Urdu** ("Kya haal hai") support to voice agents for the
Pakistani market, **fully self-hosted** (no cloud STT/TTS/LLM). No single open
speech-to-speech model speaks Urdu/Hindi today (Qwen3-Omni accepts Urdu *input*
but cannot generate Urdu/Hindi speech), so this uses a cascaded stack.

## Hard constraints

1. **Purely additive.** The existing local services (`vllm`/Qwen3-14B,
   `embeddings`/BGE-M3, `kokoro`, `speaches`) and how they are hosted **must not
   change** — same containers, ports, GPU settings, and defaults.
2. **Fully self-hosted.** No cloud providers (no Sarvam/Deepgram/ElevenLabs/NVIDIA
   cloud NIM). All new models run on the user's PC.
3. **Opt-in.** New STT/TTS providers are **selectable options** in Model
   Configuration, chosen per workflow. Existing/English workflows are unaffected;
   defaults stay Speaches (STT) + Kokoro (TTS).

## Components (all new, all additive)

| Layer | New model | How served | Footprint |
|---|---|---|---|
| STT | **AI4Bharat IndicConformer** (Urdu+Hindi, code-mix) | new `indic-stt` container, OpenAI-compatible `/v1/audio/transcriptions` | ~600M, small GPU or CPU |
| Transliteration | **AI4Bharat IndicXlit** (`ai4bharat-transliteration`) | **in-process** Python lib in API + arq worker | ~11M, CPU |
| TTS | **Piper** (Urdu `ur` + Hindi voices) | new `piper-tts` container (pipecat `PiperHttpTTSService`) | CPU-only |
| LLM / embeddings | Qwen3-14B / BGE-M3 | **unchanged** | — |

### 1. IndicConformer STT (`indic-stt` container)
- Serve IndicConformer behind an **OpenAI-compatible** `/v1/audio/transcriptions`
  endpoint (so it reuses the same client path as Speaches). Two options to settle
  in a spike: (a) VEXYL-STT (`github.com/vexyl-ai/vexyl-stt`, MIT) if it exposes
  the OpenAI shape, or (b) a ~50-line FastAPI wrapper around NeMo
  `ASRModel.from_pretrained("ai4bharat/indic-conformer-600m-multilingual")`.
- New compose service `indic-stt` (internal hostname `indic-stt:PORT`), GPU
  optional (CTC mode is fast on CPU; RNNT for best accuracy).
- Registry: register a new STT configuration (provider `INDIC` or reuse
  `SPEACHES` shape with a distinct title) with `model`, `language` (Urdu/Hindi
  set), and `base_url` defaulting to the internal hostname. Appears as a new
  **Transcriber** option.
- `service_factory.create_stt_service`: add a branch that builds an
  OpenAI-compatible STT service pointed at `indic-stt`.

### 2. IndicXlit transliteration (in-process)
- Add `ai4bharat-transliteration` to `api/requirements.txt` (installs in the
  Linux devcontainer — ignore the upstream Windows-install caveat; we run in the
  container).
- New module `api/services/transliteration/indic_xlit.py` exposing
  `to_roman(text, lang)` and `to_native(text, lang)` using a cached `XlitEngine`.
- Lazy-loaded singletons; CPU; downloads model on first use (one-time).

### 3. Piper TTS (`piper-tts` container)
- New compose service running the Piper HTTP server with Urdu + Hindi voices.
- Registry: new TTS provider `PIPER` with `model`/`voice` examples
  (`ur_PK-*`, `hi_IN-*`) and `base_url` → `piper-tts:PORT`. New **Voice** option.
- `service_factory.create_tts_service`: add a `PIPER` branch building pipecat
  `PiperHttpTTSService(base_url=..., aiohttp_session=...)`.

## Data flow & the Roman/script seam

Urdu ≈ Hindi spoken (Hustani); the difference is *script*. The pipeline runs in
**native script internally**; IndicXlit converts at the text boundaries only when
a workflow opts into Roman display/IO.

```
Inbound:  mic → IndicConformer STT → native text
                 → [if roman_mode] IndicXlit.to_roman → transcript/UI + LLM context
Brain:    Qwen3-14B (handles native OR roman) → reply text
Outbound: reply → [if reply is roman] IndicXlit.to_native → Piper TTS → audio
                 → transcript/UI shows the roman form
```

- **Recommended default:** LLM operates in native script (Qwen handles it
  cleanly), Piper speaks native, and IndicXlit romanizes **only for the
  transcript/chat display** when `roman_mode` is on. This keeps one transliteration
  seam (display) and avoids round-trip drift. Chat mode can accept Roman input and
  `to_native` it before the LLM if desired.
- A per-workflow **script toggle** (`native` | `roman`) controls whether
  transcripts/chat are romanized. Placement (STT config field vs workflow setting)
  to confirm in review.

## Surfacing as options (no default changes)
- New providers are registered **alongside** existing ones in
  `api/services/configuration/registry.py` (the fork's established additive
  pattern), so they appear as extra choices under **Model Configuration →
  Transcriber / Voice**. The served schema (`/configurations/defaults`) picks them
  up automatically — no client regen needed for defaults.
- `DEFAULT_SERVICE_PROVIDERS` and existing saved configs are **untouched**;
  Speaches/Kokoro remain the defaults. Indic is chosen per workflow.

## docker-compose-local.yaml
- **Append only** `indic-stt` and `piper-tts` services (+ any cache volume).
  The `vllm`, `embeddings`, `kokoro`, `speaches` blocks stay byte-for-byte
  identical. New internal hostnames join the same `app-network`.

## VRAM / hosting budget
- Additions are light: **Piper = CPU**, **IndicXlit = CPU (~11M)**,
  **IndicConformer = ~600M** (CPU CTC, or small GPU slice). No change to vLLM's
  `--gpu-memory-utilization 0.72`. Fits alongside the current stack.

## Error handling / fallback
- If `indic-stt` / `piper-tts` are unreachable, the workflow using them fails
  loudly (same as any misconfigured provider) — English workflows on
  Speaches/Kokoro are unaffected.
- IndicXlit failure → fall back to passing native text through unchanged (degrade
  to native display, never crash the turn).

## Testing
- Unit: `indic_xlit` to_roman/to_native round-trips for Hindi + Urdu samples
  ("Kya haal hai" ↔ native).
- Unit: `service_factory` builds the new STT/TTS services for the new providers.
- Integration: a workflow configured with IndicConformer + Piper transcribes a
  Hindi/Urdu utterance and speaks a reply; English workflow still uses
  Speaches/Kokoro unchanged (regression guard).
- Live: voice call in Urdu, confirm transcript (native and roman modes) + audio.

## Risks / open questions
- **Accent robustness** of IndicConformer on Pakistani Urdu — validate on real
  audio; Whisper large-v3 remains a fallback STT option.
- **IndicConformer Urdu output script** (Arabic vs Devanagari) — confirm; affects
  the IndicXlit direction/lang codes.
- **Code-switching** (Roman + English) raises WER ~30–50% — set expectations.
- **Serving shim**: confirm VEXYL-STT's API shape or build the FastAPI wrapper.
- **Piper Urdu voice quality/availability** — confirm a usable `ur_PK` voice.

## Out of scope (YAGNI)
- Speech-to-speech / omni models (no Urdu/Hindi speech output yet).
- Changing or replacing any existing English voice path.
- Urdu Nastaliq rendering niceties beyond transliteration.

## Implementation spikes (do first)
1. Stand up `indic-stt` and curl `/v1/audio/transcriptions` with a Hindi/Urdu clip.
2. `pip install ai4bharat-transliteration`; verify `XlitEngine` both directions in
   the container.
3. Stand up Piper HTTP with an `ur_PK` voice; synth a sample.

## Sequencing
Build only after `speakly-v1.35.0` is pushed to `origin`. Then: spikes →
implement additively → tests → live verify → (no push until the user pushes).
