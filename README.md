# YouTube Video Creator

Generates a finished video from a trending topic or any topic you type: research, script, voiceover, stock visuals, transitions, MP4 and SEO text. Next.js frontend, FastAPI backend, FFmpeg renderer.

```mermaid
flowchart LR
    A[Topic: trending or custom] --> B[Research<br/>Google/Bing News, Wikipedia]
    B --> C[Script<br/>OpenRouter free model]
    C --> D{You review<br/>script + voice}
    D --> E[Voiceover<br/>edge-tts or OpenRouter TTS]
    D --> F[Visuals<br/>Pexels / Pixabay]
    E --> G[FFmpeg render<br/>xfade transitions]
    F --> G
    G --> H[final.mp4 + title, description, tags]
```

## Requirements

- Python 3.11+
- Node 20+
- FFmpeg and ffprobe on `PATH`
- API keys: [OpenRouter](https://openrouter.ai), [Pexels](https://www.pexels.com/api/), [Pixabay](https://pixabay.com/api/docs/)
- Optional: an [Ollama cloud key](https://ollama.com/settings/keys) as an automatic text fallback

## Setup

```bash
cp .env.example .env        # fill in the three keys

cd backend
pip install -r requirements.txt
python -m uvicorn app.main:app --port 8000

cd ../frontend
npm install
npm run dev                 # http://localhost:3000
```

The frontend reads `NEXT_PUBLIC_API_URL` (default `http://localhost:8000`, see `frontend/.env.local.example`).

## Text providers and fallbacks

Script, SEO, research and trend ranking all call one chain (`backend/app/services/llm/`). Providers are tried in the order of `LLM_PROVIDERS` (default `openrouter,ollama`); one without credentials is skipped, and a provider that fails or hits its quota hands over to the next.

```mermaid
flowchart LR
    A[Text request] --> B[OpenRouter]
    B -- quota / error --> C[Ollama cloud]
    C -- error --> D[Clear error with both reasons]
    B -- ok --> E[Reply]
    C -- ok --> E
```

- The Text model dropdown groups models by provider. The provider of the model you pick answers first; the other configured providers stay as fallback.
- `OLLAMA_API_KEY` enables the Ollama fallback. `OLLAMA_MODEL` pins a model; if unset, one is chosen from Ollama's live model list.
- To add another provider: subclass `LLMProvider` (`llm/base.py`), then `register_provider("name", YourProvider)` in `llm/__init__.py` and add the name to `LLM_PROVIDERS`.
- Voice: if a chosen OpenRouter voice cannot be used, the whole video is voiced again with one Edge voice of the same gender and language (never a mix). Turn off with `TTS_FALLBACK_TO_EDGE=false`.

## Tests

```bash
cd backend && python -m pytest
```

## Notes

- **Free-tier limit:** OpenRouter allows 50 requests per day across free models, shared by script writing, research summaries and voices. The app stops early and shows the reset time when the limit is hit. Adding $10 of credit raises it to 1,000 per day.
- **Voices:** edge-tts is the default (no quota). HeyGen and Fish Audio voices via OpenRouter are opt-in in the voice picker and cost one request per scene.
- **Trends:** Google Trends and Google News feeds need no key. Reddit often blocks anonymous access and is skipped when it does.
- **Music:** `music/` is a placeholder; a track passed to the renderer is mixed in at low volume, but nothing uses it yet.
- Generated files live in `data/` (git-ignored).
