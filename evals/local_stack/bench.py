"""Self-hosted stack benchmark: per-stage latency + accuracy for the local models.

Hits the same endpoints the call pipeline uses (Speaches segmented STT, vLLM
with thinking disabled, Kokoro OpenAI-speech TTS). Run inside the workspace
container (model hosts resolve on app-network):

    python evals/local_stack/bench.py stt|llm|tts|load|all

Results: printed tables + evals/local_stack/results/<suite>-<ts>.json
"""

import asyncio
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import time
import unicodedata
from pathlib import Path

import httpx

VLLM = os.getenv("BENCH_VLLM", "http://vllm:8000/v1")
SPEACHES = os.getenv("BENCH_SPEACHES", "http://speaches:8000")
KOKORO = os.getenv("BENCH_KOKORO", "http://kokoro:8880/v1")
LLM_MODEL = os.getenv("BENCH_LLM_MODEL", "Qwen/Qwen3-14B-AWQ")
STT_MODELS = os.getenv(
    "BENCH_STT_MODELS",
    "Systran/faster-distil-whisper-large-v3,deepdml/faster-whisper-large-v3-turbo-ct2,Systran/faster-whisper-large-v3",
).split(",")
LANGS = os.getenv("BENCH_LANGS", "en,es,de,fr,it,pt,nl").split(",")
# PolyAI MINDS-14: real callers on a banking phone line, 8 kHz telephone audio.
MINDS14 = {"en": "en-US", "es": "es-ES", "de": "de-DE", "fr": "fr-FR", "it": "it-IT", "pt": "pt-PT", "nl": "nl-NL"}
# Kokoro v1.0 has no German or Dutch voice.
KOKORO_VOICES = {"en": "af_heart", "es": "ef_dora", "fr": "ff_siwis", "it": "if_sara", "pt": "pf_dora"}
N_CLIPS = int(os.getenv("BENCH_CLIPS", "10"))
REPEATS = int(os.getenv("BENCH_REPEATS", "1"))  # samples per case within one run
RUNS = int(os.getenv("BENCH_RUNS", "3"))  # full suite runs, each after its own warm-up; averaged
HERE = Path(__file__).parent
DATA = HERE / "data"
RESULTS = HERE / "results"


# ---------------------------------------------------------------- helpers
def norm_words(text: str) -> list[str]:
    text = unicodedata.normalize("NFKC", text).lower()
    return re.sub(r"[^\w\s']", " ", text).split()


def wer(ref: str, hyp: str) -> float:
    r, h = norm_words(ref), norm_words(hyp)
    if not r:
        return 0.0 if not h else 1.0
    prev = list(range(len(h) + 1))
    for i, rw in enumerate(r, 1):
        cur = [i] + [0] * len(h)
        for j, hw in enumerate(h, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (rw != hw))
        prev = cur
    return prev[-1] / len(r)


assert wer("the cat sat", "the cat sat") == 0
assert abs(wer("the cat sat", "the dog sat") - 1 / 3) < 1e-9
assert wer("Hello, World!", "hello world") == 0


def pct(values, p):
    if not values:
        return None
    s = sorted(values)
    return round(s[min(len(s) - 1, int(round(p / 100 * (len(s) - 1))))], 3)


def summary(values):
    return {"n": len(values), "p50": pct(values, 50), "p90": pct(values, 90), "mean": round(statistics.mean(values), 3) if values else None}


def average_runs(runs):
    """Average numeric leaves of same-shaped per-run result rows."""
    def avg(vals):
        if all(isinstance(v, dict) for v in vals):
            return {k: avg([v.get(k) for v in vals]) for k in vals[0]}
        nums = [v for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool)]
        return round(statistics.mean(nums), 3) if nums else vals[0]
    return [avg(list(rows)) for rows in zip(*runs)]


assert average_runs([[{"a": 1, "b": {"p50": 2}}], [{"a": 3, "b": {"p50": 4}}]]) == [{"a": 2, "b": {"p50": 3}}]


def save(suite, payload):
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / f"{suite}-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"\nsaved {out}")


# ---------------------------------------------------------------- test data
async def fetch_clips(client: httpx.AsyncClient, lang: str) -> list[dict]:
    """Download N_CLIPS MINDS-14 phone-call utterances + reference transcripts."""
    d = DATA / lang
    meta = d / "meta.json"
    if meta.exists():
        return json.loads(meta.read_text())
    d.mkdir(parents=True, exist_ok=True)
    r = await client.get(
        "https://datasets-server.huggingface.co/rows",
        params={"dataset": "PolyAI/minds14", "config": MINDS14[lang], "split": "train", "offset": 0, "length": N_CLIPS},
        timeout=60,
    )
    r.raise_for_status()
    clips = []
    for i, row in enumerate(x["row"] for x in r.json()["rows"]):
        audio = row["audio"][0] if isinstance(row["audio"], list) else row["audio"]
        raw = d / f"{i:02d}.src"
        raw.write_bytes((await client.get(audio["src"], timeout=60)).content)
        wav = d / f"{i:02d}.wav"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(raw), "-ar", "16000", "-ac", "1", str(wav)], check=True)
        raw.unlink()
        clips.append({"wav": str(wav), "text": row["transcription"]})
    meta.write_text(json.dumps(clips, ensure_ascii=False))
    return clips


# ---------------------------------------------------------------- STT
async def transcribe(client, model, lang, wav: Path) -> tuple[str, float]:
    t = time.perf_counter()
    r = await client.post(
        f"{SPEACHES}/v1/audio/transcriptions",
        data={"model": model, "language": lang, "response_format": "json"},
        files={"file": (wav.name, wav.read_bytes(), "audio/wav")},
        timeout=120,
    )
    r.raise_for_status()
    return r.json()["text"], time.perf_counter() - t


async def suite_stt(client):
    data = {lang: await fetch_clips(client, lang) for lang in LANGS}
    rows = []
    for model in STT_MODELS:
        await client.post(f"{SPEACHES}/v1/models/{model}", timeout=1800)  # download if missing
        await transcribe(client, model, "en", Path(data[LANGS[0]][0]["wav"]))  # load + warm up
        for lang, clips in data.items():
            lat, errs, samples = [], [], []
            for c in clips:
                text, secs = await transcribe(client, model, lang, Path(c["wav"]))
                lat.append(secs)
                errs.append(wer(c["text"], text))
                samples.append({"ref": c["text"], "hyp": text, "secs": round(secs, 3), "audio_secs": round(os.path.getsize(c["wav"]) / 32000, 1)})
            row = {"model": model, "lang": lang, "latency": summary(lat), "wer": round(statistics.mean(errs), 3), "samples": samples[:3]}
            rows.append(row)
            print(f"{model:45} {lang} WER {row['wer']:.1%}  p50 {row['latency']['p50']}s  p90 {row['latency']['p90']}s")
    # No unload: Speaches 'DELETE /api/ps' then reload wedged transcription. Restart
    # the speaches container between models instead (see run_all.sh).
    save("stt", rows)
    return rows


# ---------------------------------------------------------------- LLM
SYSTEM = (
    "You are the phone receptionist of Lakeside Family Clinic. Today is Monday, 2026-11-16. "
    "Opening hours: Monday to Friday 08:00-18:00; closed on weekends. "
    "Always reply in the caller's language, in one or two short spoken sentences, no lists or markdown. "
    "Use the tools to book or cancel appointments, check whether lab reports are ready, transfer to staff, "
    "or end the call. Never invent patient data; ask for any missing detail before calling a tool. "
    "Dates as YYYY-MM-DD, times as HH:MM (24h)."
) + os.getenv(
    "BENCH_SYSTEM_EXTRA",
    # mirrors TOOL_PREFERENCE_INSTRUCTION in api/services/workflow/pipecat_engine_context_composer.py
    "\n\nWhen the caller's latest message satisfies a function's description, call that function instead of replying in text. "
    "This includes goodbyes: if a function ends the call, call it rather than saying goodbye yourself, because the next step speaks for you.",
)


def _fn(name, desc, props, required):
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": {"type": "object", "properties": {k: {"type": "string", "description": v} for k, v in props.items()}, "required": required}}}


TOOLS = [
    _fn("book_appointment", "Book an appointment for a patient.", {"patient_name": "Full name", "date": "YYYY-MM-DD", "time": "HH:MM 24h", "reason": "Reason for visit"}, ["patient_name", "date", "time"]),
    _fn("check_report_status", "Check whether a patient's lab report is ready.", {"patient_name": "Full name", "date_of_birth": "YYYY-MM-DD"}, ["patient_name", "date_of_birth"]),
    _fn("cancel_appointment", "Cancel a patient's existing appointment.", {"patient_name": "Full name", "date": "YYYY-MM-DD of the appointment"}, ["patient_name", "date"]),
    _fn("transfer_to_human", "Transfer the caller to a staff member.", {"reason": "Why"}, []),
    _fn("end_call", os.getenv("BENCH_END_CALL_DESC", "The caller wants to end the conversation (says goodbye, thanks, or has nothing else). Call this immediately instead of replying - the goodbye is spoken for you, so do not say goodbye yourself."), {}, []),
]

# (expected tool or None, {arg: substring that must appear (any of '|' options)}, turns per language)
CASES = [
    ("book_appointment", {"patient_name": "smith", "date": "2026-11-20", "time": "15:"}, {
        "en": ["Hi, this is John Smith. I'd like to book a check-up on November 20th at 3 pm."],
        "es": ["Hola, soy John Smith. Quiero reservar una revisión el 20 de noviembre a las 3 de la tarde."],
        "de": ["Hallo, hier ist John Smith. Ich möchte einen Kontrolltermin am 20. November um 15 Uhr buchen."],
        "fr": ["Bonjour, c'est John Smith. Je voudrais prendre rendez-vous pour un bilan le 20 novembre à 15 heures."]}),
    ("check_report_status", {"patient_name": "garcia|garcía", "date_of_birth": "1985-03-12"}, {
        "en": ["Is my blood test ready? Maria Garcia, born March 12, 1985."],
        "es": ["¿Ya están mis análisis de sangre? Soy Maria Garcia, nacida el 12 de marzo de 1985."],
        "de": ["Ist mein Bluttest schon fertig? Maria Garcia, geboren am 12. März 1985."],
        "fr": ["Est-ce que ma prise de sang est prête ? Maria Garcia, née le 12 mars 1985."]}),
    ("cancel_appointment", {"patient_name": "müller|muller|mueller", "date": "2026-11-25"}, {
        "en": ["I need to cancel my appointment on November 25th. My name is Anna Müller."],
        "es": ["Necesito cancelar mi cita del 25 de noviembre. Me llamo Anna Müller."],
        "de": ["Ich muss meinen Termin am 25. November absagen. Mein Name ist Anna Müller."],
        "fr": ["Je dois annuler mon rendez-vous du 25 novembre. Je m'appelle Anna Müller."]}),
    ("transfer_to_human", {}, {
        "en": ["Can I please speak to a real person?"],
        "es": ["¿Puedo hablar con una persona, por favor?"],
        "de": ["Kann ich bitte mit einem Menschen sprechen?"],
        "fr": ["Est-ce que je peux parler à une vraie personne, s'il vous plaît ?"]}),
    (None, {}, {
        "en": ["What time do you open on Saturday?"],
        "es": ["¿A qué hora abren el sábado?"],
        "de": ["Wann haben Sie am Samstag geöffnet?"],
        "fr": ["À quelle heure ouvrez-vous le samedi ?"]}),
    (None, {}, {
        "en": ["I'd like to book an appointment."],
        "es": ["Quisiera pedir una cita."],
        "de": ["Ich möchte einen Termin vereinbaren."],
        "fr": ["Je voudrais prendre un rendez-vous."]}),
    ("end_call", {}, {
        "en": ["No, that's everything. Thanks, goodbye!"],
        "es": ["No, eso es todo. ¡Gracias, adiós!"],
        "de": ["Nein, das ist alles. Danke, auf Wiederhören!"],
        "fr": ["Non, c'est tout. Merci, au revoir !"]}),
    ("check_report_status", {"patient_name": "johnson", "date_of_birth": "1970-07-04"}, {
        "en": ["Are my lab results in?", "Sure, may I have your full name and date of birth?", "Peter Johnson, 4th of July 1970."],
        "es": ["¿Ya tienen mis resultados de laboratorio?", "Claro, ¿me dice su nombre completo y fecha de nacimiento?", "Peter Johnson, 4 de julio de 1970."],
        "de": ["Sind meine Laborergebnisse schon da?", "Gerne, wie ist Ihr vollständiger Name und Ihr Geburtsdatum?", "Peter Johnson, 4. Juli 1970."],
        "fr": ["Est-ce que mes résultats d'analyses sont arrivés ?", "Bien sûr, pouvez-vous me donner votre nom complet et votre date de naissance ?", "Peter Johnson, le 4 juillet 1970."]}),
]

# shortcut: stopword vote, not a real language-ID model; fine for 1-2 sentence replies.
STOPWORDS = {
    "en": "the you your is are to and what can for we our please have i a it".split(),
    "es": "el los las que y en por su usted para es una un con le del".split(),
    "de": "der die das und ist sie ihr ihre nicht ein eine wir zu mit haben bitte".split(),
    "fr": "le les et vous votre est pour une un nous pas que avec des au".split(),
}


def detect_lang(text):
    """Best stopword match, or None when the reply has too little signal to tell."""
    words = norm_words(text)
    hits = {lang: sum(w in sw for w in words) for lang, sw in STOPWORDS.items()}
    best = max(hits, key=hits.get)
    return best if hits[best] >= 2 and list(hits.values()).count(hits[best]) == 1 else None


async def chat(client, messages, tools=TOOLS, max_tokens=150):
    """Stream one completion; return timing + tool calls + text."""
    body = {"model": LLM_MODEL, "messages": messages, "stream": True, "max_tokens": max_tokens,
            "chat_template_kwargs": {"enable_thinking": False}}
    if tools:
        body["tools"] = tools
    t0 = time.perf_counter()
    first = first_sentence = None
    text, calls = "", {}
    async with client.stream("POST", f"{VLLM}/chat/completions", json=body, timeout=120) as r:
        r.raise_for_status()
        async for line in r.aiter_lines():
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue
            choices = json.loads(line[6:]).get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta", {})
            if (delta.get("content") or delta.get("tool_calls")) and first is None:
                first = time.perf_counter() - t0
            if delta.get("content"):
                text += delta["content"]
                if first_sentence is None and re.search(r"[.!?¿¡]\s*$|[.!?]\s", text):
                    first_sentence = time.perf_counter() - t0
            for tc in delta.get("tool_calls") or []:
                c = calls.setdefault(tc.get("index", 0), {"name": "", "arguments": ""})
                fn = tc.get("function") or {}
                c["name"] += fn.get("name") or ""
                c["arguments"] += fn.get("arguments") or ""
    total = time.perf_counter() - t0
    return {"ttft": first, "first_sentence": first_sentence or total, "total": total, "text": text.strip(), "calls": list(calls.values())}


def grade(case, lang, out):
    expected, args, _ = case
    names = [c["name"] for c in out["calls"]]
    if expected is None:
        ok = not names and bool(out["text"])
        why = "" if ok else f"unexpected tool {names}"
    elif names[:1] != [expected]:
        ok, why = False, f"expected {expected}, got {names or 'text: ' + out['text'][:80]}"
    else:
        try:
            got = json.loads(out["calls"][0]["arguments"] or "{}")
        except json.JSONDecodeError:
            got = None
        if not isinstance(got, dict):
            ok, why = False, f"bad JSON args {out['calls'][0]['arguments'][:80]}"
        else:
            missing = [k for k, v in args.items() if not any(o in str(got.get(k, "")).lower() for o in v.split("|"))]
            ok, why = not missing, f"wrong/missing {missing}: {got}" if missing else ""
    lang_ok = None
    detected = detect_lang(out["text"]) if out["text"] and not out["calls"] else None
    if detected:
        lang_ok = detected == lang
        if not lang_ok:
            print(f"  LANG {lang}->{detected}: {out['text'][:100]}")
    return ok, why, lang_ok


async def suite_llm(client):
    await chat(client, [{"role": "user", "content": "hi"}])  # warm
    rows, fails = [], []
    for lang in ("en", "es", "de", "fr"):
        passed = total = lang_hits = lang_total = 0
        ttft, sent = [], []
        for case in CASES:
            turns = case[2][lang]
            msgs = [{"role": "system", "content": SYSTEM}] + [
                {"role": "user" if i % 2 == 0 else "assistant", "content": t} for i, t in enumerate(turns)
            ]
            for _ in range(REPEATS):
                out = await chat(client, msgs)
                ok, why, lang_ok = grade(case, lang, out)
                total += 1
                passed += ok
                if lang_ok is not None:
                    lang_total += 1
                    lang_hits += lang_ok
                ttft.append(out["ttft"] or out["total"])
                if not out["calls"]:
                    sent.append(out["first_sentence"])
                if not ok:
                    fails.append({"lang": lang, "turns": turns, "why": why})
        row = {"lang": lang, "tool_accuracy": round(passed / total, 3), "n": total,
               "reply_language_ok": round(lang_hits / lang_total, 3) if lang_total else None,
               "ttft": summary(ttft), "first_sentence": summary(sent)}
        rows.append(row)
        print(f"LLM {lang}: tools {row['tool_accuracy']:.0%} ({passed}/{total})  reply-lang {row['reply_language_ok']}  "
              f"TTFT p50 {row['ttft']['p50']}s  first-sentence p50 {row['first_sentence']['p50']}s")
    for f in fails:
        print("  FAIL", f["lang"], f["turns"][-1][:60], "->", f["why"][:160])
    save("llm", {"rows": rows, "fails": fails})
    return rows


# ---------------------------------------------------------------- TTS
TTS_TEXT = {
    "en": "Thank you for calling. Your appointment is confirmed for Thursday at three in the afternoon.",
    "es": "Gracias por llamar. Su cita está confirmada para el jueves a las tres de la tarde.",
    "fr": "Merci de votre appel. Votre rendez-vous est confirmé pour jeudi à quinze heures.",
    "it": "Grazie per aver chiamato. Il suo appuntamento è confermato per giovedì alle tre del pomeriggio.",
    "pt": "Obrigado por ligar. A sua consulta está confirmada para quinta-feira às três da tarde.",
}


async def speak(client, voice, text):
    t0 = time.perf_counter()
    first, buf = None, bytearray()
    body = {"model": "kokoro", "voice": voice, "input": text, "response_format": "wav", "stream": True}
    async with client.stream("POST", f"{KOKORO}/audio/speech", json=body, timeout=120) as r:
        r.raise_for_status()
        async for chunk in r.aiter_bytes():
            if first is None and chunk:
                first = time.perf_counter() - t0
            buf.extend(chunk)
    return first, time.perf_counter() - t0, bytes(buf)


async def suite_tts(client):
    judge = STT_MODELS[-1]  # most accurate STT judges intelligibility
    await client.post(f"{SPEACHES}/v1/models/{judge}", timeout=1800)
    rows = []
    for lang, voice in KOKORO_VOICES.items():
        await speak(client, voice, "warm up")
        ttfb, totals, errs = [], [], []
        for _ in range(REPEATS):
            first, total, audio = await speak(client, voice, TTS_TEXT[lang])
            ttfb.append(first)
            totals.append(total)
            wav = DATA / f"tts-{lang}.wav"
            wav.parent.mkdir(parents=True, exist_ok=True)
            wav.write_bytes(audio)
            text, _ = await transcribe(client, judge, lang, wav)
            errs.append(wer(TTS_TEXT[lang], text))
        row = {"lang": lang, "voice": voice, "ttfb": summary(ttfb), "total": summary(totals), "roundtrip_wer": round(statistics.mean(errs), 3)}
        rows.append(row)
        print(f"TTS kokoro {voice:9} {lang}: TTFB p50 {row['ttfb']['p50']}s  full {row['total']['p50']}s  round-trip WER {row['roundtrip_wer']:.1%}")
    save("tts", rows)
    return rows


# ---------------------------------------------------------------- concurrency
async def one_turn(client, stt_model, clip, lang):
    t0 = time.perf_counter()
    text, stt = await transcribe(client, stt_model, lang, Path(clip["wav"]))
    out = await chat(client, [{"role": "system", "content": SYSTEM}, {"role": "user", "content": text}], max_tokens=60)
    reply = re.split(r"(?<=[.!?])\s", out["text"] or "One moment please.")[0]
    first_audio, _, _ = await speak(client, KOKORO_VOICES.get(lang, "af_heart"), reply)
    return {"stt": stt, "llm_first_sentence": out["first_sentence"], "tts_ttfb": first_audio,
            "model_total": stt + out["first_sentence"] + first_audio, "wall": time.perf_counter() - t0}


async def suite_load(client):
    stt_model = os.getenv("BENCH_LOAD_STT", "deepdml/faster-whisper-large-v3-turbo-ct2")
    clips = await fetch_clips(client, "en")
    await transcribe(client, stt_model, "en", Path(clips[0]["wav"]))
    rows = []
    for n in (1, 2, 4, 8):
        async def caller(k):
            return [await one_turn(client, stt_model, clips[(k + t) % len(clips)], "en") for t in range(3)]
        turns = [t for call in await asyncio.gather(*(caller(k) for k in range(n))) for t in call]
        row = {"concurrent_calls": n, "stt_model": stt_model, **{k: summary([t[k] for t in turns]) for k in turns[0]}}
        rows.append(row)
        print(f"{n} calls: model latency p50 {row['model_total']['p50']}s p90 {row['model_total']['p90']}s "
              f"(stt {row['stt']['p50']} + llm {row['llm_first_sentence']['p50']} + tts {row['tts_ttfb']['p50']})")
    save("load", rows)
    return rows


# ---------------------------------------------------------------- TURN END
VAD_STOP_SECS = 0.2  # run_pipeline.py VADParams(stop_secs=0.2)
OLD_TURN_WAIT = VAD_STOP_SECS + 0.6  # + SpeechTimeoutUserTurnStopStrategy default
SMART_TURN_FALLBACK = float(os.getenv("BENCH_SMART_TURN_STOP_SECS", "1.0"))  # = DEFAULT_SMART_TURN_STOP_SECS


async def suite_turn(client):
    """How long the caller waits before the bot may answer, with Smart Turn v3.

    Whole utterance -> should be judged complete (bot answers after VAD stop +
    inference). First half -> should be judged incomplete (bot keeps listening
    instead of cutting the caller off; old fixed timeout would have answered).
    """
    import numpy as np
    import soundfile as sf
    from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3

    analyzer = LocalSmartTurnAnalyzerV3(sample_rate=16000)
    analyzer._predict_endpoint(np.zeros(16000, dtype=np.float32))  # warm-up
    rows = []
    for lang in LANGS:
        complete, cut_detected, infer, waits = [], [], [], []
        for c in await fetch_clips(client, lang):
            audio, _ = sf.read(c["wav"], dtype="float32")
            t = time.perf_counter()
            done = analyzer._predict_endpoint(audio)["prediction"] == 1
            infer.append(time.perf_counter() - t)
            complete.append(done)
            waits.append(VAD_STOP_SECS + infer[-1] if done else SMART_TURN_FALLBACK)
            half = analyzer._predict_endpoint(audio[: len(audio) // 2])["prediction"] == 0
            cut_detected.append(half)
        row = {"lang": lang, "complete_detected": round(statistics.mean(complete), 2),
               "midsentence_held": round(statistics.mean(cut_detected), 2),
               "inference": summary(infer), "turn_wait": summary(waits), "old_turn_wait": OLD_TURN_WAIT}
        rows.append(row)
        print(f"TURN {lang}: finished-detected {row['complete_detected']:.0%}  mid-sentence-held "
              f"{row['midsentence_held']:.0%}  wait p50 {row['turn_wait']['p50']}s (old {OLD_TURN_WAIT}s)  "
              f"inference p50 {row['inference']['p50']}s")
    save("turn", rows)
    return rows


async def main():
    suites = {"stt": suite_stt, "llm": suite_llm, "tts": suite_tts, "load": suite_load, "turn": suite_turn}
    pick = sys.argv[1] if len(sys.argv) > 1 else "all"
    if not shutil.which("ffmpeg"):
        sys.exit("ffmpeg is required")
    async with httpx.AsyncClient() as client:
        for name in suites if pick == "all" else [pick]:
            runs = []
            for i in range(RUNS):
                print(f"\n=== {name} run {i + 1}/{RUNS} ===")
                runs.append(await suites[name](client))
            print(f"\n=== {name} AVERAGE of {RUNS} runs ===")
            average = average_runs(runs)
            for row in average:
                print(json.dumps({k: v for k, v in row.items() if k != "samples"}, ensure_ascii=False))
            save(f"{name}-average", {"runs": runs, "average": average})


if __name__ == "__main__":
    asyncio.run(main())
