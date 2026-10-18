# INTERVUE candidate platform

INTERVUE is an ember/orange cinematic interview-practice experience with a FastAPI backend, Supabase, a server-mediated Gemini Live voice path, and Groq structured evaluation. The original text interview remains available.

## Phase 3 voice capabilities

- Authenticated, one-use WebSocket connection tickets; the Gemini API key never reaches the browser
- Browser microphone capture with echo cancellation, 16 kHz PCM resampling, mute, actual-input metering, 24 kHz PCM playback, interruption handling, and bounded reconnect attempts
- Current `gemini-3.8-live` bidirectional audio integration with input/output transcription, automatic turn detection, context compression, and connection cleanup
- CV-aware system context that explicitly falls back to role configuration when extraction is unavailable
- RLS-protected ordered transcript turns with idempotency keys and two-account isolation
- Primary `openai/gpt-oss-20b` Groq coaching evaluation validated against exact saved transcript excerpts
- Separate Groq, optional Gemini, and heuristic evaluation records; heuristic scoring remains the final fallback
- Bounded structured-evaluation retries for transient network, timeout, rate-limit, and 5xx failures. Permanent 4xx responses and invalid structured output are not retried.
- Privacy-safe timing diagnostics record durations, attempt status, prompt byte count, and turn count without logging transcript, CV, or credential content.
- Voice history, resumption from the saved transcript, report display, and JSON download

Raw microphone audio is streamed for live processing and is not stored by INTERVUE.

## Phase 2 capabilities

- Supabase email/password registration, login, logout, token refresh, session restoration, and password-reset request
- FastAPI health and authenticated profile endpoints
- Private PDF, DOCX, and UTF-8 TXT CV storage with signature checks, a 5 MB limit, text extraction, deletion, and ownership enforcement
- Persisted interview configuration, questions, answers, completion, resume, history, and reports
- Transparent deterministic heuristic scoring, clearly labeled as non-AI practice guidance
- Versioned PostgreSQL migration with UUID keys, constraints, foreign keys, RLS on every candidate table, and private Storage policies
- Responsive loading, error, expired-session, and empty states while preserving the original visual design

## Required configuration

Create a Supabase project, then copy `.env.example` to `backend/.env` and replace the placeholders:

```powershell
Copy-Item .env.example backend\.env
```

Required values:

- `SUPABASE_URL`: Project URL from the Supabase Connect dialog.
- `SUPABASE_ANON_KEY`: The publishable key (`sb_publishable_...`) or legacy anon key. It is sent only by the backend in this architecture.
- `FRONTEND_ORIGINS`: Comma-separated exact frontend origins.
- `GEMINI_API_KEY`: server-only key from Google AI Studio. Required for live voice and Gemini evaluation.
- `GROQ_API_KEY`: server-only Groq key used for primary structured evaluation.
- `GROQ_EVALUATION_MODEL`: defaults to the structured-output-capable `openai/gpt-oss-20b` model.
- `GEMINI_EVALUATION_FALLBACK_ENABLED`: defaults to `false`; enable only to allow Gemini evaluation after Groq fails.
- `GEMINI_EVALUATION_BUDGET_SECONDS`, `GEMINI_EVALUATION_ATTEMPT_TIMEOUT_SECONDS`, and `GEMINI_EVALUATION_MAX_ATTEMPTS`: bounded evaluation reliability controls (defaults: 120 seconds total, 50 seconds per attempt, 2 attempts).
- `GEMINI_LIVE_MODEL` and `GEMINI_EVALUATION_MODEL`: documented model IDs; defaults are in `.env.example`.

`SUPABASE_SERVER_KEY` and `DATABASE_URL` are optional and are not required by the current runtime. Never place a service-role or secret key in frontend code.

## Supabase setup

1. Install the Supabase CLI and authenticate it.
2. Link this directory to your project: `supabase link --project-ref YOUR_PROJECT_REF`.
3. Review both files in `supabase/migrations`, including the Phase 3 voice migration.
4. Apply it with `supabase db push`.
5. In Authentication settings, enable Email/Password and configure your Site URL and password-reset redirect URL.
6. Confirm the `candidate-cvs` bucket is private. The migration creates it and its ownership policies.
7. If your Data API settings do not expose newly created `public` tables automatically, expose the `public` schema. The migration already grants table access to `authenticated`, with RLS enforcing row ownership.
8. Run Supabase security/performance advisors before production deployment.

## Backend installation and run

From `backend` in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Check `http://127.0.0.1:8000/api/health`. The response reports whether Supabase and Gemini configuration were found, without disclosing credentials.

## Frontend run

From the project root, use a static server on an origin listed in `FRONTEND_ORIGINS`:

```powershell
python -m http.server 5500
```

Open `http://127.0.0.1:5500`. Localhost automatically uses `http://127.0.0.1:8000/api`.

## Production deployment

The frontend is a static Vercel deployment. Its build reads `INTERVUE_API_URL` and writes only that public API origin into `dist/runtime-config.js`. Set it to the persistent FastAPI origin ending in `/api`, for example `https://api.example.com/api`. It must use HTTPS; voice connections are then derived as WSS automatically.

The FastAPI backend must run on a host that supports persistent WebSocket connections. Do not put `GEMINI_API_KEY`, `GROQ_API_KEY`, Supabase server credentials, or the FastAPI service itself into the static frontend deployment. Configure these backend environment variable names on the backend host:

- `SUPABASE_URL`
- `SUPABASE_ANON_KEY`
- `FRONTEND_ORIGINS`
- `GEMINI_API_KEY`
- `GEMINI_LIVE_MODEL`
- `GROQ_API_KEY`
- `GROQ_EVALUATION_MODEL`

Set `FRONTEND_ORIGINS` to the exact Vercel production origin. Preview deployments require either explicitly approved preview origins or a separate preview backend policy; wildcard CORS is intentionally unsupported.

Build the frontend locally with:

```powershell
$env:INTERVUE_API_URL='https://your-fastapi-host.example/api'
npm run build
```

If `INTERVUE_API_URL` is absent, the public shell still builds but authenticated API actions display a configuration error rather than contacting a visitor's localhost.

The repository includes `render.yaml` for the persistent FastAPI/WebSocket backend. It creates a single-worker Python web service on Render's free plan, binds Uvicorn to Render's assigned port, and checks `/api/health`. During the initial Blueprint setup, enter the four server-side values requested by Render: `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `GEMINI_API_KEY`, and `GROQ_API_KEY`. Never put these values in GitHub or Vercel frontend variables.

## Tests

From `backend`:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

The automated suite uses a mocked Supabase gateway. It verifies application behavior and ownership propagation without connecting to a real project. Live Auth, Database, RLS, and Storage verification requires real credentials and the applied migration.

For an explicit live verification, configure the four `LIVE_TEST_USER_*` variables shown in `.env.example` with two confirmed, non-production accounts, then run from `backend`:

```powershell
$env:PYTHONPATH=(Get-Location).Path
.\.venv\Scripts\python.exe .\tests\live_phase2.py
```

The live test never prints credentials or tokens. It uploads and deletes a temporary CV, creates and deletes a temporary interview, tests cross-account reads and writes, and logs both test sessions out. The two Auth accounts themselves are preserved.

Run the Phase 3 Supabase persistence/RLS check separately:

```powershell
$env:PYTHONPATH=(Get-Location).Path
.\.venv\Scripts\python.exe .\tests\live_phase3.py
```

The automated provider tests use mocks and schema validation. A real Gemini smoke test requires `GEMINI_API_KEY`, a working microphone, and an interactive browser; do not treat mocked tests as proof that real-time audio works.

## Security notes

- The backend resolves identity by validating the bearer token with Supabase Auth; it never trusts a frontend-submitted user ID.
- Database and Storage operations use the candidate access token, allowing RLS to remain the authorization boundary.
- Session tokens are stored in browser local storage for this static frontend. For a same-origin production deployment, consider a hardened BFF design with secure, HttpOnly cookies.
- CV extraction is synchronous and text-based. Scanned/image-only PDFs return an explicit extraction failure; they are never described as analyzed.
- Gemini scores are coaching estimates, not objective measurements or hiring recommendations. Protected traits, emotion, and employability must not be inferred from voice.
- The one-use voice ticket store is process-local. Production with multiple backend workers requires a shared short-lived store such as Redis.
- The browser receives only an opaque INTERVUE ticket. The long-lived Gemini key remains server-side.

