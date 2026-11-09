# Mia Home AI — working API-connected prototype

Mobile-friendly photo upload, AI styling feedback, and photo-to-photo makeover generation.

## Run locally

1. Install Python 3.10+.
2. In this folder: `python -m pip install -r requirements.txt`
3. Copy `.env.example` to `.env` and set your own `OPENAI_API_KEY` (do not commit `.env`).
4. Run: `uvicorn app:app --reload`
5. Open http://127.0.0.1:8000

The backend calls the OpenAI Responses API for photo advice and the Images Edit API for makeovers. API usage is billed separately from ChatGPT. Image model access and API options depend on your OpenAI API account; if an endpoint rejects a model/parameter, review current API documentation and update the relevant environment variable or call.

## Deploy

Deploy to a Python-compatible host with persistent web service support (e.g. Render/Railway/Fly.io). Set the OPENAI_API_KEY environment variable in the hosting provider's secret manager. Start command: `uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000}`. Do not expose this prototype publicly without the protections below.

## IMPORTANT: Not yet safe for public commercial launch

This is a **private prototype**, not a production SaaS. Before public release, implement authentication, per-user generation quotas, billing/paywall, rate limiting, bot protection, abuse moderation, observability, spend ceilings, data-retention/privacy policies, photo consent, and proper error monitoring. The prototype does not persist uploaded photos on disk, but photos and generated data are processed by OpenAI according to applicable API terms and retention settings. It has no automatic checkout or account system. AI image edits can inadvertently alter room geometry or architectural details; always label outputs as concepts, not buildable plans. Never expose API keys in browser JavaScript.

## Testing

- `python -m compileall app.py` for syntax.
- `GET /health` returns `api_configured` status.
- Test live advice and generation only after providing a valid API key; live API calls incur charges.
