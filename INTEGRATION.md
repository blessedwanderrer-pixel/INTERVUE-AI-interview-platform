# Integration contract — Phase 3

## Current credentials

- `SUPABASE_URL`: server-side FastAPI configuration.
- `SUPABASE_ANON_KEY`: Supabase publishable/anon key used with candidate bearer tokens.
- `SUPABASE_SERVER_KEY`: optional and server-only; unused by current candidate routes.
- `DATABASE_URL`: optional and server-only; unused during normal runtime.
- `GEMINI_API_KEY`: required server-only credential for Gemini Live and Gemini evaluation.
- `GEMINI_LIVE_MODEL`: defaults to the currently documented `gemini-3.8-live`.
- `GEMINI_EVALUATION_MODEL`: defaults to `gemini-3.8-flash` for structured coaching JSON.

The browser never receives a service-role or secret key. FastAPI validates every access token through Supabase Auth and derives the candidate UUID from the validated user response.

## API boundary

- `GET /api/health`
- `GET /api/me`
- `POST /api/auth/register`, `/login`, `/refresh`, `/logout`, `/password-reset`, `/password-update`
- `GET|POST /api/cvs`, `DELETE /api/cvs/{cv_id}`
- `GET|POST /api/interviews`
- `GET /api/interviews/{id}`
- `POST /api/interviews/{id}/answers`, `/complete`
- `GET /api/interviews/{id}/report`
- `POST /api/voice/sessions/{id}` creates a one-use, short-lived WebSocket ticket
- `WS /api/voice/ws?ticket=...` proxies bounded PCM audio and transcription events
- `GET /api/voice/sessions/{id}/transcript`
- `POST /api/voice/sessions/{id}/complete`

All candidate data endpoints require `Authorization: Bearer <access_token>`.

## Gemini Live boundary

FastAPI validates the Supabase bearer token and interview ownership before issuing an opaque, one-use ticket with a 60-second default lifetime. The browser uses that ticket to connect to INTERVUE's WebSocket. FastAPI then opens the Gemini Live connection with the server-only key and relays raw PCM bytes. Tickets are never accepted twice, active connections are bounded per candidate, chunks and session duration are limited, and cleanup runs on disconnect.

The browser sends raw little-endian PCM16 at 16 kHz and receives PCM16 at 24 kHz. INTERVUE does not persist raw audio. Saved input/output transcriptions are the durable recovery boundary. A reconnect starts a new Gemini connection with the saved transcript as context.

## Evidence schema

Groq is the primary structured evaluation provider. Evidence contains a transcript turn UUID and an exact excerpt; the server rejects any reference whose excerpt is not present in that turn. Missing support is represented in `missing_evidence`. Groq, optional Gemini, and heuristic reports are stored separately and labeled by source.

Evaluation requests use a 120-second total safety budget with at most two 50-second attempts and jittered exponential backoff. Only transient timeouts, network failures, rate limits, and provider 5xx responses are retried. A persisted Gemini evaluation is reused on repeated completion requests, and the database uniqueness constraint remains the final duplicate barrier. If evaluation cannot complete, the explicitly labeled heuristic report remains available.

