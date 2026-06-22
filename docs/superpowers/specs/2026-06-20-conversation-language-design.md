# Per-Agent "Conversation Language" Selector — Design

**Date:** 2026-06-20
**Status:** Approved
**Fork:** Speakly (BasimBashir/Speakly-)
**Depends on:** the self-hosted Indic voice stack on `feat/indic-voice`
(IndicConformer STT, Piper TTS, uroman romanization).

## Problem & goal

Today, getting an Urdu conversation requires hand-wiring three org-wide settings
(STT language, the agent's reply-language prompt, TTS voice) and keeping them in
sync — and the agent still **replies in English** unless its prompt says
otherwise. We want a single **per-agent "Conversation Language"** dropdown
(English / Urdu / Hindi / …) chosen at agent creation that auto-wires all three,
so one English agent and one Urdu agent can coexist under the same org Models
config.

## Hard constraints

1. **Additive + regression-safe.** English (the default) is a pure no-op —
   today's behavior, no config override, no prompt change.
2. **Per-agent overlay.** The org Models page stays the baseline; the agent's
   language is an overlay applied per call. Existing agents (no
   `conversation_language`) behave exactly as now.
3. **Self-hosted.** Profiles reference only local providers (IndicConformer,
   Piper, Speaches/Kokoro, vLLM/Qwen).

## Components

### 1. Language profiles registry — `api/services/configuration/conversation_language.py`
A built-in mapping `code -> ConversationLanguageProfile`:

| code | label | reply instruction | STT override | TTS override | output_script |
|------|-------|-------------------|--------------|--------------|---------------|
| `english` | English | _(none)_ | _(none — org default)_ | _(none)_ | native |
| `urdu` | Urdu | "Respond only in Urdu, using Urdu script." | IndicConformer, lang `ur`, `http://indic-stt:8000/v1` | Piper, `ur_PK-fasih-medium`, `http://piper-tts:5000` | native |
| `hindi` | Hindi | "Respond only in Hindi, using Devanagari script." | IndicConformer, lang `hi` | Kokoro (Speaches) Hindi voice | native |

```python
@dataclass(frozen=True)
class ConversationLanguageProfile:
    code: str
    label: str
    reply_instruction: str | None          # appended to system prompt; None = no-op
    stt_override: dict | None               # raw STT config dict, or None = org default
    tts_override: dict | None               # raw TTS config dict, or None = org default

PROFILES: dict[str, ConversationLanguageProfile] = { ... }
DEFAULT_LANGUAGE = "english"

def get_profile(code: str | None) -> ConversationLanguageProfile  # falls back to english
def supported_languages() -> list[dict]   # [{"code","label"}] for the UI
```
Each override is a **full** STT/TTS config dict (provider + model + language/voice
+ base_url), so switching language switches providers — an Urdu agent and an
English agent coexist under one org config. Adding a language = one entry here.

### 2. Per-agent field + resolution wiring
- `conversation_language` (string, default `"english"`) stored in the agent's
  `workflow_configurations` JSON (no DB migration — it's a JSON column).
- In `get_effective_ai_model_configuration_for_workflow(...)`: after resolving
  the base effective config, read `workflow_configurations["conversation_language"]`;
  if non-English, build typed STT/TTS configs from the profile overrides (via the
  registry, like `resolve._build_section_from_override`) and set them on the
  effective config. English → unchanged.
- This runs for both voice and (where applicable) text paths, so STT/TTS for that
  agent's calls match the language.

### 3. Reply-language prompt injection
- `compose_system_prompt_for_node(...)` gains a `conversation_language` argument
  (threaded from `PipecatEngine`, which already holds `workflow_configurations`).
- When the profile has a `reply_instruction`, append it as a prompt part (after
  global + node prompts). English → nothing appended.
- This is the fix for "currently replies in English."

### 4. UI — the dropdown
- New endpoint `GET /api/v1/configurations/conversation-languages` →
  `[{ "code", "label" }]` from `supported_languages()`.
- A "Conversation Language" `<Select>` on the agent settings (in
  `ModelConfigurationDialog` / workflow settings). On save it writes
  `conversation_language` into `workflow_configurations`. Defaults to English.
- No generated-client change needed beyond the new endpoint (regenerate client).

### 5. Error handling & testing
- **English = pure no-op** → regression-safe; existing/English agents untouched.
- If a profile's provider/container is unavailable, that agent's call fails like
  any misconfigured provider; other agents are unaffected.
- Unknown/old `conversation_language` value → `get_profile` falls back to English.
- Tests:
  - registry: `get_profile("urdu")` returns IndicConformer+Piper overrides;
    `get_profile(None)`/unknown → english.
  - resolution: a workflow with `conversation_language="urdu"` yields effective
    STT=IndicConformer(ur) + TTS=Piper; `english` leaves org config unchanged.
  - prompt: `compose_system_prompt_for_node(..., conversation_language="urdu")`
    contains "Respond only in Urdu"; `english` adds nothing.
  - endpoint returns the supported list including urdu/hindi/english.

## Out of scope (YAGNI)
- Admin UI to edit/define profiles (profiles are code-defined for now).
- Auto language detection mid-call.
- Romanized **reply** generation (LLM replies in native script; the existing
  `output_script` toggle handles romanized *display* separately).

## Notes
- Hindi TTS uses an existing Kokoro Hindi voice (exact voice id confirmed during
  implementation); Urdu TTS uses Piper `ur_PK-fasih`.
- Builds on `feat/indic-voice`; not pushed.
