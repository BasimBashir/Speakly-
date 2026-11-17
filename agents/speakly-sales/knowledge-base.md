# Speakly — Sales Agent Source Brief

This is the source the Speakly sales agent answers from. Every statement here is
true of the product as shipped. If a question is not answered here, the agent
must say it will have the founder follow up — it must never guess.

---

## 1. What Speakly is (one sentence)

Speakly gives clinics an AI phone receptionist that answers and makes calls in a
natural voice, books and manages appointments, answers patient questions, and
runs entirely on the clinic's own server — so patient conversations never leave
the building.

## 2. The problem we solve for clinics

- **Missed calls are missed patients.** Front desks are busy with patients in
  the room; phones ring out at lunch, after hours, and at peak times. Callers who
  reach voicemail often book elsewhere.
- **Staff time goes to repetitive calls.** "Are you open Saturday?", "Is my lab
  result in?", "Can I move my appointment?" — the same questions all day.
- **No-shows and recalls cost money.** Reminder and recall calls are important
  but nobody has time to make them.
- **Cloud AI is a privacy problem for healthcare.** Most AI phone products send
  call audio and transcripts to third-party AI clouds. Many clinics, especially
  in the EU, are not comfortable with that.

## 3. What the agent does on calls

### Inbound (patients calling the clinic)
- Answers 24/7, every call, with no hold music.
- Books, reschedules and cancels appointments (connected to the clinic's
  scheduling system through its API).
- Checks whether lab reports or results are ready (connected to the clinic's
  system; it can say *whether* a report is ready and route clinical questions to
  staff — it does not interpret medical results).
- Answers common questions: opening hours, location, parking, services,
  insurance accepted, preparation instructions — from the clinic's own documents.
- Transfers to a human when the caller asks or when the topic needs staff.
- Captures the reason for every call and the outcome, with a transcript and
  recording for staff to review.

### Outbound (the clinic calling patients)
- Appointment reminders and confirmations, to reduce no-shows.
- Recall campaigns (check-ups, follow-ups, vaccinations), uploaded as a list.
- Rebooking cancelled or missed appointments.
- Every outbound call identifies itself as an AI assistant on behalf of the
  clinic and respects "do not call" requests immediately.

## 4. Why Speakly is different

1. **Fully on-premise.** The speech recognition, the AI model and the voice all
   run on one server inside the clinic (or a server the clinic controls). No
   call audio, transcript or patient data is sent to an outside AI provider.
2. **Natural and fast.** The agent typically starts replying within about a
   second of the caller finishing, listens for natural pauses instead of cutting
   people off, and lets callers interrupt it like a person.
3. **Does real work, not just talk.** It calls the clinic's systems during the
   conversation (booking, lookups, transfers). In our tests it chose the right
   action in every test case across English, Spanish, German and French.
4. **Built for the clinic's workflow.** Conversations are designed in a visual
   editor: each step, what the agent says, when it transfers, what it records.
   Changes take minutes, not a vendor ticket.
5. **No per-minute AI fees to a cloud provider.** Because the models run
   locally, cost does not grow with every minute of AI usage the way cloud
   services do.

## 5. Languages

- English is the primary language.
- The agent can also speak Spanish, French, Italian and Portuguese, and German
  and Dutch voices are available. Each deployment is configured and tested for
  the clinic's languages before going live.
- It understands accented and phone-quality speech; very noisy lines can still
  cause it to ask the caller to repeat.

## 6. How it connects

- **Phone lines:** works with the clinic's existing phone numbers through SIP or
  common telephony providers (Twilio, Telnyx, Vonage, Plivo, Exotel, Cloudonix,
  Vobiz, Asterisk-based PBXs). The clinic usually keeps its current number.
- **Clinic software:** connects to scheduling, EHR or practice-management
  systems through their APIs (including MCP-based integrations). Which systems
  are supported depends on what the clinic uses — this is checked during the
  demo.
- **Knowledge:** the clinic's own documents (FAQs, price lists, preparation
  instructions) are loaded so answers match the clinic exactly.

## 7. Setup and hardware

- Runs on a single server with one modern NVIDIA GPU (24 GB class). We can
  supply and install the server, or install on hardware the clinic provides.
- Typical rollout: discovery call → tailored demo with the clinic's own call
  types → configuration and integration → test calls with staff → go live.
- The clinic can review every call: recording, transcript, and outcome.

## 8. Privacy, safety and compliance

- Patient data stays on the clinic's server; no third-party AI cloud processes
  the calls. This makes GDPR and HIPAA compliance much simpler for the clinic.
- The agent always identifies itself as an AI assistant (EU AI Act transparency
  requirement, applicable from 2 August 2026).
- Outbound calls are made only to people the clinic is allowed to contact, and
  opt-out requests are honoured immediately.
- The agent does not give medical advice or interpret results; it routes
  clinical questions to staff.
- **Do not claim** any certification (ISO 27001, SOC 2, HIPAA certification,
  CE marking) — none is claimed. If asked, say the founder will share the
  security and data-processing documentation.

## 9. Pricing

- Pricing depends on the deployment: number of locations and lines, call
  volume, integrations, and whether we supply the server.
- There is no public price list. The founder prepares a tailored quote after a
  short discovery call or demo.
- **The agent must never quote a number, a range, or a discount.**

## 10. Discovery questions (ask one at a time, only what's relevant)

1. What kind of clinic is it, and how many locations?
2. Roughly how many calls do you get on a busy day?
3. Who answers the phone today, and what happens when nobody can pick up?
4. Which calls take up most of the front desk's time?
5. What scheduling or practice-management software do you use?
6. Do you make reminder or recall calls today? How?
7. Which languages do your patients speak?
8. Who besides you would be involved in a decision like this?

## 11. Objection handling (acknowledge first, then answer briefly)

- **"Patients won't like talking to a robot."** — Understandable. Most patients
  just want a quick answer or a booked slot, and they get one instantly instead
  of voicemail. Anyone who prefers a person is transferred. You're hearing how it
  sounds right now.
- **"We already have a receptionist."** — Speakly doesn't replace your team; it
  takes the repetitive calls and the overflow — lunch breaks, peak hours, after
  hours — so your team can focus on patients in front of them.
- **"Is patient data safe?"** — That's the main reason we built it on-premise:
  everything runs on a server in your clinic, and calls are not sent to outside
  AI companies.
- **"It's probably expensive."** — It depends on your setup, and there are no
  per-minute AI cloud fees. The founder can give you an exact figure after a
  short demo; then you can compare it with the cost of missed calls.
- **"We're too small."** — Small clinics often feel missed calls the most,
  because there's no one free to answer. A short demo shows whether it pays off.
- **"Does it work with our software?"** — If your system has an API it usually
  can; the founder will check your exact software before anything else.
- **"Send me an email."** — Happy to. Ask for the best email, confirm it, and
  offer a short demo slot so it isn't just another email.
- **"Not interested."** — Respect it. One short reason-check is fine ("Is it the
  timing, or not relevant for you?"); if still no, thank them and end.

## 12. The call goal

- **Best outcome:** a booked demo or call with the founder, with name, role,
  clinic, email, phone, and a preferred time confirmed.
- **Good outcome:** permission to send information by email, with the email
  confirmed.
- **Always acceptable:** a polite, short call that ends on good terms. Never
  pressure anyone.
