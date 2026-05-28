# Speakly

**The open-source, self-hostable alternative to Vapi & Retell** — build production voice agents with a drag-and-drop workflow builder. From zero to a working bot in under 2 minutes.

> Built on top of [Dograh](https://github.com/dograh-hq/dograh) open-source voice AI platform.

<p align="center">
  <a href="https://app.dograh.com">
    <img src="https://img.shields.io/badge/▶_Try_the_Cloud-app.dograh.com-2563eb?style=for-the-badge" alt="Try the Cloud">
  </a>
  &nbsp;
  <a href="#-get-started">
    <img src="https://img.shields.io/badge/⚡_Self--host_in_60s-One_command-111827?style=for-the-badge" alt="Self-host in 60s">
  </a>
  &nbsp;
  <a href="https://join.slack.com/t/dograh-community/shared_invite/zt-3czr47sw5-MSg1J0kJ7IMPOCHF~03auQ">
    <img src="https://img.shields.io/badge/💬_Join_Slack-Community-4A154B?style=for-the-badge&logo=slack" alt="Join Slack">
  </a>
</p>

<p align="center">
  <a href="https://docs.dograh.com">📖 Docs</a> &nbsp;·&nbsp;
  <a href="LICENSE">📜 BSD 2-Clause</a> &nbsp;·&nbsp;
  <a href="README.zh-CN.md">🌐 中文</a>
</p>

<p align="center">
  <img src="docs/images/hero.gif" alt="Dograh in action — build a workflow, launch a voice agent, talk to it" width="80%">
</p>

- **100% open source**, self-hostable — no vendor lock-in, unlike Vapi or Retell
- **Full control & transparency** — every line of code is open, with flexible LLM / TTS / STT integration
- **Maintained by YC alumni and exit founders**, committed to keeping voice AI open

<p align="center">
  <a href="https://trendshift.io/repositories/31007" target="_blank"><img src="https://trendshift.io/api/badge/repositories/31007" alt="dograh-hq%2Fdograh | Trendshift" style="width: 250px; height: 55px;" width="250" height="55"/></a>
</p>

## 🎥 Featured

<div align="center">
  <a href="https://www.youtube.com/watch?v=xD9JEvfCH9k">
    <img src="https://img.youtube.com/vi/xD9JEvfCH9k/maxresdefault.jpg" alt="Dograh featured by Better Stack" width="80%" style="border-radius: 8px; box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);">
  </a>
  <br>
  <em>Featured by <strong>Better Stack</strong> — a hands-on look at Dograh</em>
</div>

<details>
<summary>📺 Prefer a 2-minute product walkthrough? Click here.</summary>

<div align="center">
  <a href="https://youtu.be/9gPneyf9M9w">
    <img src="docs/images/video_thumbnail_1.png" alt="Watch Dograh AI Demo Video" width="70%" style="border-radius: 8px; box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);">
  </a>
</div>

</details>

## ⚖️ Dograh vs Vapi vs Retell

An honest comparison on the axes that matter most to teams evaluating voice AI platforms.

|  | **Dograh** | **Vapi** | **Retell** |
|---|---|---|---|
| **License** | BSD 2-Clause (open source) | Proprietary | Proprietary |
| **Self-hostable** | ✅ Yes — one Docker command | ❌ SaaS only | ❌ SaaS only |
| **Pricing** | Free (self-host) · usage-based (cloud) | Per-minute SaaS | Per-minute SaaS |
| **Bring your own LLM / STT / TTS** | ✅ Any provider, or use Dograh's stack | Configurable within their integrations | Configurable within their integrations |
| **Source-level customization** | ✅ Every line is yours to modify | ❌ Closed source | ❌ Closed source |
| **Data residency** | Your infra, your rules | Their cloud | Their cloud |
| **Vendor lock-in** | None | Full | Full |


## 🚀 Get Started

### Option A — Windows one-click (recommended for local development)

Double-click `start.bat` in the repo root. It will:

1. Verify Node.js, Docker Desktop are installed and the Docker daemon is running
2. Install `@devcontainers/cli` globally if missing (one-time)
3. `devcontainer up --workspace-folder .` — brings up every container (postgres, redis, minio, workspace, plus the optional GPU model stack)
4. Launch the backend in a console window (`bash scripts/start_services_dev.sh`)
5. Launch the UI in another console window (`npm run dev -- --hostname 0.0.0.0`)
6. Poll `http://localhost:8000/api/v1/health` until ready
7. Open `http://127.0.0.1:3000` in your default browser

First run pulls ~13 GB of images and model weights (15–30 min depending on connection). Subsequent runs start in under a minute — model weights are cached in Docker named volumes.

### Option B — Quick Docker (no local development)

```bash
curl -o docker-compose.yaml https://raw.githubusercontent.com/dograh-hq/dograh/main/docker-compose.yaml && REGISTRY=ghcr.io/dograh-hq ENABLE_TELEMETRY=true docker compose up --pull always
```

> **Note**
> First startup may take 2-3 minutes to download all images. Once running, open http://localhost:3010 to create your first AI voice assistant!
> For common issues and solutions, see 🔧 **[Troubleshooting](docs/troubleshooting.md)**.

> **Note**
> We collect anonymous usage data to improve the product. You can opt out by setting `ENABLE_TELEMETRY` to `false`.

> **Note**
> If you wish to run the platform on a remote server instead, check our [Documentation](https://docs.dograh.com/deployment/docker#option-2:-remote-server-deployment).

### 🎙️ Your First Voice Bot

1. Open [http://localhost:3010](http://localhost:3010) in your browser.
2. Pick **Inbound** or **Outbound**, name your bot (e.g. _Lead Qualification_), and describe the use case in 5–10 words (e.g. _Screen insurance form submissions for purchase intent_).
3. Click **Web Call** — you're talking to your bot.

> 🔑 **No API keys needed.** Dograh ships with auto-generated keys and its own LLM / TTS / STT stack. Connect your own keys for LLM, TTS, STT, or Telephony (e.g. Twilio, Vonage, Telnyx) anytime.

## Features

### Voice Capabilities

- Telephony: Built-in telephony integration like Twilio, Vonage, Vobiz, Cloudonix (easily add others), with support for transferring calls to human agents
- Languages: English support (expandable to other languages)
- Custom Models: Bring your own TTS/STT models
- Real-time Processing: Low-latency voice interactions

### Knowledge Base & Document Intelligence

- **Upload any document** (PDF, DOCX, XLSX, CSV, TXT) and have your agent reference it during calls
- **Per-document AI summaries (DocCards)** — on upload, the system extracts a structured card (title, key facts, entities, FAQs, suggested agent uses, topics) so the agent knows what each doc contains without dumping the full text into every prompt
- **Auto-built organization knowledge index** — a compact table of contents over all your docs is injected into the agent's system prompt at call start, scoped by inbound/outbound intent
- **Chunked vector search or full-document retrieval** — pick per-document based on size and use case
- **Model-agnostic extraction** — uses your configured LLM (Dograh hosted, OpenAI, Azure, OpenRouter, or local via vLLM/Ollama)

### Developer Experience

- Zero Config Start: Auto-generated API keys for instant testing
- Python-Based: Built on Python for easy customization
- Docker-First: Containerized for consistent deployments
- Modular Architecture: Swap components as needed
- **Windows one-click launcher**: double-click `start.bat` to bring up everything

### Testing & Quality

- **Test Mode**: Try your agent end-to-end before publishing, with no production calls or data affected
- **In-Dashboard Web Calls**: Talk to your bot directly while building — no telephony setup required
- **QA Node**: A built-in workflow node that analyzes prompt quality across your other nodes

### Fully Local AI Stack (Optional, NVIDIA GPU)

Run the entire AI stack on your own GPU instead of cloud services. With an RTX 3090 (24 GB) or similar, `start.bat` will bring up:

| Component | Model | Container |
|---|---|---|
| LLM (with tool calling + MCP) | Qwen3-14B-AWQ | vLLM (`awq_marlin` kernel) |
| Embeddings | BGE-M3 (1024-dim) | Hugging Face TEI |
| TTS | Kokoro-82M (67 voices) | Kokoro-FastAPI |
| STT | Faster Distil-Whisper Large v3 | Speaches |

All four are OpenAI-compatible HTTP endpoints — configure them via the **Models** page in the UI. See [`docs/contribution/local-models.mdx`](docs/contribution/local-models.mdx) for the full setup and tuning guide.

## Deployment Options

### Local Development

Refer [Local Setup](https://docs.dograh.com/contribution/setup)

### Self-Hosted Deployment

For detailed deployment instructions including remote server setup with HTTPS, see our [Docker Deployment Guide](https://docs.dograh.com/deployment/docker).

### Cloud Version

Visit [https://www.dograh.com](https://www.dograh.com/) for our managed cloud offering.

## 📚Documentation

You can go to [https://docs.dograh.com](https://docs.dograh.com/) for our documentation.

## 📦 SDKs

- **Python SDK** — [pypi.org/project/dograh-sdk](https://pypi.org/project/dograh-sdk/)
- **Node SDK** — [npmjs.com/package/@dograh/sdk](https://www.npmjs.com/package/@dograh/sdk)

## 🤝Community & Support

> 👋 **Coming from the Better Stack video?** Drop your use case in our [pinned GitHub Discussion](https://github.com/orgs/dograh-hq/discussions/291) — we read every reply and the founders personally onboard early adopters.

- **Slack** — the cornerstone of Dograh AI contributions. Connect with maintainers, discuss features before coding, get help with setup, and stay current on contribution sprints.
- **GitHub Discussions** — share use cases, ask questions, swap workflow recipes.
- **GitHub Issues** — report bugs or request features.

👉 Join us → [Dograh Community Slack](https://join.slack.com/t/dograh-community/shared_invite/zt-3czr47sw5-MSg1J0kJ7IMPOCHF~03auQ)

## 🙌 Contributing

We love contributions! Dograh AI is 100% open source and we intend to keep it that way.

### Getting Started

- Fork the repository
- Create your feature branch (git checkout -b feature/AmazingFeature)
- Commit your changes (git commit -m 'Add some AmazingFeature')
- Push to the branch (git push origin feature/AmazingFeature)
- Open a Pull Request

## ⭐ Star History

<a href="https://star-history.com/#dograh-hq/dograh&Date">
  <img src="https://api.star-history.com/svg?repos=dograh-hq/dograh&type=Date" alt="Dograh star history" width="80%">
</a>

## 📄 License

Dograh AI is licensed under the [BSD 2-Clause License](LICENSE)- the same license as projects that were used in building Dograh AI, ensuring compatibility and freedom to use, modify, and distribute.

## 🏢 About

Built with ❤️ by **Dograh** (Zansat Technologies Private Limited)
Founded by YC alumni and exit founders committed to keeping voice AI open and accessible to everyone.

<br><br><br>

  <p align="center">
    <a href="https://github.com/dograh-hq/dograh/stargazers">⭐ Star us on GitHub</a> |
    <a href="https://app.dograh.com">☁️ Try Cloud Version</a> |
    <a href="https://join.slack.com/t/dograh-community/shared_invite/zt-3czr47sw5-MSg1J0kJ7IMPOCHF~03auQ">💬 Join Slack</a>
  </p>
