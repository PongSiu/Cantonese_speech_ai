# Cantonese Speech Therapy AI (TheraLingua)

A Flask web app that helps users practise Cantonese pronunciation, with AI
pronunciation scoring (Azure Speech + Gemini fallback), assessment reports,
badges, daily challenges, and a therapist dashboard.

## Setup

1. Create a virtual environment and install dependencies:
   ```bash
   py -3.13 -m venv .venv
   .venv\Scripts\activate        # Windows
   pip install -r requirements.txt
   ```

2. Copy your API keys into `.env` (the file is git-ignored — never commit it):
   ```
   GEMINI_API_KEY=...            # Google AI Studio key (Gemini)
   AZURE_SPEECH_KEY=...          # Azure Speech (optional)
   AZURE_SPEECH_REGION=eastasia
   AZURE_TEXT_KEY=...            # Azure Language (optional)
   AZURE_TEXT_ENDPOINT=...
   ```
   The app now **starts even if the keys are empty** — AI features simply
   degrade gracefully (you'll see a "AI features disabled" message) instead of
   crashing the server. Fill the keys to enable scoring / reports / TTS.

3. Run locally:
   ```bash
   py app.py
   # http://localhost:3000
   ```

## Configuration (environment variables)

All optional — sensible defaults preserve the original local behaviour.

| Variable | Default | Purpose |
|---|---|---|
| `SECRET_KEY` | auto-generated `.flask_secret` | Flask session signing key. Set this in production. |
| `FLASK_DEBUG` | `1` | Set to `0` in production to disable the interactive debugger. |
| `FLASK_HOST` | `0.0.0.0` | Bind address. |
| `FLASK_PORT` | `3000` | Port. |
| `THERAPIST_INVITE_CODE` | `THERA2024` | Code required to register a therapist account. |

## Database

Uses local SQLite (`cantonese_therapy.db`), created automatically on first run
via `database.init_db()`. The DB file and uploaded avatars are git-ignored so
user data stays out of version control.
