"""Create the Speakly sales agent (one workflow for inbound + outbound calls).

Uploads knowledge-base.md to the knowledge base, creates the workflow, sets its
configuration and publishes it — all through the app's own API, so the same
validation runs as in the UI. Re-runnable on any deployment.

    DOGRAH_API_KEY=... python agents/speakly-sales/create_agent.py

Env: DOGRAH_API_KEY (Settings -> API keys), DOGRAH_API_URL
(default http://localhost:8000/api/v1), DOGRAH_KB_DOC_UUID (optional: reuse an
already-uploaded knowledge-base document instead of uploading a new copy),
DOGRAH_WORKFLOW_ID (optional: update this workflow and publish a new version
instead of creating a new one).

Outbound campaign CSVs may include `first_name` and `clinic_name` columns; both
are optional (the prompts fall back gracefully).
"""

import os
import sys
import time
from pathlib import Path

import httpx

API = os.getenv("DOGRAH_API_URL", "http://localhost:8000/api/v1").rstrip("/")
HERE = Path(__file__).parent
NAME = "Speakly Sales Agent (inbound + outbound)"

AGENT = "{{agent_name | fallback:Mia}}"
COMPANY = "{{company_name | fallback:Speakly}}"
FOUNDER = "{{founder_name | fallback:Basim}}"
NO_GOODBYE = (
    " Call it rather than saying goodbye yourself, because the next step speaks for you."
)

GLOBAL_PROMPT = f"""# WHO YOU ARE
You are {AGENT}, the AI phone assistant of {COMPANY}. {COMPANY} gives clinics an on-premise AI phone receptionist. This call is itself a live demo: you are the product, so how you sound is part of the pitch.

# HOW YOU SPEAK (this is a real phone call)
- Keep every reply short: one or two sentences, under 30 words. Ask one question at a time, then stop and listen.
- Sound like a warm, confident professional: contractions, plain words, a brief acknowledgement before answering ("Got it.", "That makes sense.", "Good question."). Never use the same acknowledgement twice in a row.
- Never use lists, bullet points, headings, emojis or markdown. Say numbers, dates and times the way a person would ("next Tuesday at three p.m.").
- If you did not understand something, say so and ask them to repeat. Never guess a name, email or number.
- Reply in the caller's language if they speak Spanish, French, Italian, Portuguese, German or Dutch; otherwise English.

# HONESTY RULES (never break these)
- You are an AI assistant. Say so in your first sentence on every call, and confirm it plainly whenever asked. Never claim to be human.
- Only state facts from this prompt or the knowledge base. For anything else say {FOUNDER} can confirm it, and move on.
- Never quote a price, a range or a discount. Pricing is tailored; {FOUNDER}, the founder, prepares a quote after a short demo.
- Never promise that Speakly works with the caller's specific software. Say {FOUNDER} will check their exact systems and confirm.
- You cannot look anything up, check availability, put anyone on hold, or connect or transfer anyone during this call. Never say "one moment", "let me check", "hold on", or "connecting you". If asked to be connected with {FOUNDER}, explain that {FOUNDER} will contact them right after the call, and collect their contact details so he can.
- Never claim certifications such as ISO 27001, SOC 2 or HIPAA certification. Offer that {FOUNDER} shares the security documentation.
- Never pressure anyone. Respect a no. If they ask not to be contacted again, apologise briefly, confirm they will not be contacted again, and end the call.

# KEY FACTS (use these directly)
- What it does: answers and makes clinic phone calls around the clock in a natural voice. It books, reschedules and cancels appointments, checks whether lab reports are ready, answers questions from the clinic's own documents, transfers to staff, and makes reminder and recall calls.
- Why it is different: it runs entirely on a server inside the clinic, so call audio and patient data never go to an outside AI company. It replies in about a second, connects to the clinic's scheduling and practice software through their APIs, and works with the clinic's existing phone number.
- Languages: English, plus Spanish, French, Italian, Portuguese, German and Dutch, set up per clinic.
- Goal of this call: book a short demo call with {FOUNDER}, the founder, or at least get permission to email information.

# CALL CONTEXT
- Call direction: {{{{call_direction | fallback:inbound}}}} (outbound means you called them; inbound means they called you).
- Contact name if known: {{{{first_name | fallback:unknown}}}}. Clinic if known: {{{{clinic_name | fallback:unknown}}}}.
- Now: {{{{current_weekday}}}}, {{{{current_time}}}} (UTC)."""

OPENING_PROMPT = f"""# THIS STEP: OPENING
Check the call direction in the call context.

If OUTBOUND (you called them):
- If you hear a voicemail greeting or a beep, leave one short message: your name, that you are an AI assistant from {COMPANY}, that {COMPANY} helps clinics answer every call with an on-premise AI receptionist, and that {FOUNDER} will try again. Then end the call.
- Otherwise open with your name, that you are an AI assistant calling from {COMPANY}, and a permission question. For example: "Hi, this is Mia, an AI assistant from Speakly. Fun fact, this call is our product. We help clinics answer every phone call. Do you have thirty seconds?" Use their name if you know it.
- If they are busy, ask when a better time would be, confirm it, then end the call.
- If they ask why you are calling, say you are reaching out to clinics about missed calls and front-desk workload.

If INBOUND (they called you):
- Greet them warmly, for example: "Thanks for calling Speakly, this is Mia, the AI assistant. How can I help?" Then find out who they are and what they need.

Once they are willing to talk, ask their name and clinic if you do not know them yet."""

DISCOVERY_PROMPT = f"""# THIS STEP: UNDERSTAND THEIR CLINIC, THEN SHOW THE FIT
1. Ask two or three discovery questions, one at a time, picking what is relevant: the type of clinic and number of locations, how many calls they get on a busy day, who answers today and what happens when nobody can, which calls take up the most staff time, what scheduling software they use, which languages their patients speak.
2. Reflect back what you heard in one sentence, then connect one or two benefits to their specific pain. For example: "So lunchtime calls go to voicemail. That's exactly the gap Speakly fills: it picks up every call and books straight into your system."
3. Answer questions and objections: acknowledge, answer in one or two sentences, then check it landed. For details beyond your key facts, such as integrations, security, setup, hardware or rollout, look them up in the knowledge base before answering. If it is not there, say {FOUNDER} will confirm.
4. Once they show interest or their main questions are answered, propose the next step: "The easiest next step is a fifteen-minute demo with {FOUNDER}, our founder, using your clinic's own call types. Would that be useful?"
If they want to speak with {FOUNDER} or be connected with him: do not offer to check his availability or hold — say {FOUNDER} will contact them right after this call, then collect their details: name, clinic, best email or phone, and a good time to reach them.
If they decline twice or want to stop, end politely. Never repeat the same pitch."""

BOOKING_PROMPT = f"""# THIS STEP: CONFIRM THE FOLLOW-UP DETAILS
Collect, one question at a time, only what you do not already have:
1. Their full name and role.
2. Clinic name and city (the city tells you their time zone).
3. Best email address. Ask them to spell it, then read it back slowly, letter by letter for anything unusual.
4. Best phone number. On an outbound call, ask whether the number you called is the best one.
5. A preferred day and time for a fifteen-minute call, in their time zone. Skip this if they only want an email.
Then sum it all up in one or two sentences and ask whether you got it right. Fix anything they correct.
Tell them {FOUNDER} will email them to confirm. You are noting their preference; never say a slot is booked, scheduled or confirmed."""

CLOSING_PROMPT = f"""The conversation is over. Say one short, warm closing sentence in the caller's language, under fifteen words, that fits what happened. If a demo or an email was agreed, mention that {FOUNDER} will be in touch. If they asked not to be contacted again, confirm they will not be. Then stop."""

EXTRACT = [
    ("contact_name", "string", "Full name of the person on the call."),
    ("contact_role", "string", "Their role, e.g. practice manager, owner, doctor."),
    ("clinic_name", "string", "Name of the clinic."),
    ("clinic_city", "string", "City or region of the clinic."),
    ("clinic_type", "string", "Type of clinic and number of locations."),
    ("email", "string", "Email exactly as spelled and confirmed on the call."),
    ("phone", "string", "Best phone number they confirmed."),
    ("preferred_demo_time", "string", "Preferred day and time for the demo, with time zone."),
    ("callback_time", "string", "When to call back, if they were busy."),
    ("call_volume", "string", "Calls per busy day, as they described it."),
    ("current_phone_handling", "string", "Who answers today and what happens with missed calls."),
    ("scheduling_software", "string", "Scheduling or practice-management software they use."),
    ("patient_languages", "string", "Languages their patients speak."),
    ("pain_points", "string", "Their main problems with phone calls, in their words."),
    ("objections", "string", "Concerns or objections they raised."),
    ("interest_level", "string", "One of: hot, warm, cold, not_interested."),
    ("next_step", "string", "One of: demo_requested, email_info, callback, none."),
    ("do_not_call", "boolean", "True if they asked not to be contacted again."),
    ("reached_voicemail", "boolean", "True if the call reached voicemail."),
]


def definition(document_uuid: str) -> dict:
    def node(id_, type_, x, y, data):
        return {"id": id_, "type": type_, "position": {"x": x, "y": y}, "data": data}

    def edge(id_, source, target, label, condition):
        return {"id": id_, "source": source, "target": target,
                "data": {"label": label, "condition": condition}}

    return {
        "nodes": [
            node("global", "globalNode", -420, 0, {"name": "Persona and rules", "prompt": GLOBAL_PROMPT}),
            node("opening", "startCall", 0, 0, {
                "name": "Opening", "prompt": OPENING_PROMPT, "add_global_prompt": True,
                # Listen first: on outbound calls the callee usually says "Hello?".
                "delayed_start": True, "delayed_start_duration": 1.0,
                "allow_interrupt": True, "allow_greeting_interrupt": True,
            }),
            node("discovery", "agentNode", 0, 260, {
                "name": "Discovery and pitch", "prompt": DISCOVERY_PROMPT,
                "add_global_prompt": True, "allow_interrupt": True,
                "document_uuids": [document_uuid],
            }),
            node("booking", "agentNode", 0, 520, {
                "name": "Book follow-up", "prompt": BOOKING_PROMPT,
                "add_global_prompt": True, "allow_interrupt": True,
            }),
            node("end", "endCall", 420, 520, {
                "name": "End call", "prompt": CLOSING_PROMPT, "add_global_prompt": False,
                "extraction_enabled": True,
                "extraction_prompt": "Extract only what the person actually said on the call. "
                                     "Leave a field empty if it was not mentioned.",
                "extraction_variables": [{"name": n, "type": t, "prompt": p} for n, t, p in EXTRACT],
            }),
        ],
        "edges": [
            edge("e-open-disc", "opening", "discovery", "Start discovery",
                 "The person is willing to talk: they agreed, asked what this is about, "
                 "or asked a question about Speakly."),
            edge("e-open-end", "opening", "end", "End call",
                 "The person is busy and a callback time was agreed or declined, is not "
                 "interested, is the wrong person or number, asked not to be contacted, "
                 "you just left a voicemail message, or they say goodbye." + NO_GOODBYE),
            edge("e-disc-book", "discovery", "booking", "Book follow-up",
                 "The person agreed to a demo or call with the founder, or asked to "
                 "receive information by email."),
            edge("e-disc-end", "discovery", "end", "End call",
                 "The person is not interested after you checked once, wants to stop, "
                 "asked not to be contacted, or says goodbye." + NO_GOODBYE),
            edge("e-book-end", "booking", "end", "End call",
                 "All details are confirmed and you told them what happens next, or "
                 "the person wants to stop." + NO_GOODBYE),
        ],
    }


CONFIGURATIONS = {
    "max_call_duration": 600,
    "turn_stop_strategy": "turn_analyzer",
    "smart_turn_stop_secs": 0.8,
    "conversation_language": "english",
    "call_dispositions": [
        {"code": "demo_requested", "description": "The person agreed to a demo or call with the founder."},
        {"code": "info_requested", "description": "The person asked for information by email but no demo."},
        {"code": "callback_requested", "description": "The person was busy and asked to be called back later."},
        {"code": "not_interested", "description": "The person declined or said it is not relevant for them."},
        {"code": "do_not_call", "description": "The person asked not to be contacted again."},
        {"code": "voicemail_detected", "description": "The call reached voicemail or an answering machine."},
        {"code": "wrong_number", "description": "The call reached the wrong person or a non-clinic number."},
    ],
}
TEMPLATE_VARIABLES = {"agent_name": "Mia", "company_name": "Speakly", "founder_name": "Basim"}


def check(response: httpx.Response) -> dict:
    if response.is_error:
        sys.exit(f"{response.request.method} {response.request.url} -> {response.status_code}: {response.text}")
    return response.json()


def upload_knowledge_base(client: httpx.Client) -> str:
    doc = HERE / "knowledge-base.md"
    up = check(client.post("/knowledge-base/upload-url",
                           json={"filename": "speakly-sales-brief.md", "mime_type": "text/markdown"}))
    put = httpx.put(up["upload_url"], content=doc.read_bytes(),
                    headers={"Content-Type": "text/markdown"}, timeout=60)
    if put.is_error:
        sys.exit(f"Upload to storage failed: {put.status_code} {put.text}")
    check(client.post("/knowledge-base/process-document", json={
        "document_uuid": up["document_uuid"], "s3_key": up["s3_key"],
        "retrieval_mode": "chunked", "doc_type": "script", "intended_use": ["inbound", "outbound"],
        "user_description": "Speakly product brief for the sales agent: what Speakly does, "
                            "differentiators, languages, integrations, setup, compliance, "
                            "pricing policy, discovery questions and objection handling.",
    }))
    for _ in range(120):
        status = check(client.get(f"/knowledge-base/documents/{up['document_uuid']}"))
        if status["processing_status"] == "completed":
            return up["document_uuid"]
        if status["processing_status"] == "failed":
            sys.exit(f"Knowledge-base processing failed: {status.get('processing_error')}")
        time.sleep(2)
    sys.exit("Knowledge-base processing did not finish within 4 minutes")


def main() -> None:
    key = os.environ.get("DOGRAH_API_KEY") or sys.exit("Set DOGRAH_API_KEY")
    with httpx.Client(base_url=API, headers={"X-API-Key": key}, timeout=60) as client:
        doc_uuid = os.environ.get("DOGRAH_KB_DOC_UUID") or upload_knowledge_base(client)
        print(f"knowledge base document: {doc_uuid}")
        wf_id = os.environ.get("DOGRAH_WORKFLOW_ID")
        version = "v2" if wf_id else "v1"
        description = ("Tightened honesty rules: no fake mid-call actions or holds, "
                       "booking details when the caller asks for the founder"
                       if wf_id else "Initial Speakly sales agent")
        if wf_id:
            check(client.put(f"/workflow/{wf_id}", json={
                "workflow_definition": definition(doc_uuid),
                "workflow_configurations": CONFIGURATIONS,
                "template_context_variables": TEMPLATE_VARIABLES,
            }))
        else:
            wf = check(client.post("/workflow/create/definition",
                                   json={"name": NAME, "workflow_definition": definition(doc_uuid)}))
            wf_id = wf["id"]
            check(client.put(f"/workflow/{wf_id}", json={
                "workflow_definition": definition(doc_uuid),
                "workflow_configurations": CONFIGURATIONS,
                "template_context_variables": TEMPLATE_VARIABLES,
            }))
        check(client.post(f"/workflow/{wf_id}/publish",
                          json={"version_name": version, "change_description": description}))
        verb = "updated and published" if os.environ.get("DOGRAH_WORKFLOW_ID") else "created and published"
        print(f"workflow {wf_id} {verb}: {NAME}")


if __name__ == "__main__":
    main()
