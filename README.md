# AI Insurance Sales Assistant (MVP+)

This app is built from your SRD and now includes:

- MAIN intake/orchestration workflow
- Module A/B/C generation flow
- Shared subflows (validate, normalize, compliance, notify, error)
- Case Data Layer with **memory** or **PostgreSQL/Supabase** backend
- Prompt registry layer
- LLM provider adapter (**Mock / OpenAI / Gemini**)
- n8n workflow JSON for end-to-end orchestration
- EV charging customer-support chatbot endpoints for LINE in Thai

Reference documentation:

- `docs/EV_CHATBOT_ARCHITECTURE.md`
- `docs/CUSTOMER_HANDOVER_CHECKLIST.md`

## Structure

- `app/core/service.py`: MAIN_Case_Intake_And_Orchestration
- `app/core/bootstrap.py`: dependency wiring by env
- `app/core/config.py`: environment configuration
- `app/data/repository.py`: InMemory + Postgres/Supabase repository
- `app/llm/prompt_registry.py`: prompt registry loader
- `app/llm/providers.py`: provider clients (Mock/OpenAI/Gemini)
- `prompts/registry.json`: versioned prompt templates
- `n8n/insurance_assistant_workflow.json`: importable n8n workflow
- `n8n/ev_line_support_workflow.json`: importable n8n workflow for LINE chatbot orchestration

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
```

Run app:

```bash
uvicorn app.main:app --reload
```

## Data Backend

### Memory (default)

```bash
export DATA_BACKEND=memory
```

### PostgreSQL or Supabase

```bash
export DATA_BACKEND=postgres
export DATABASE_URL="postgresql://USER:PASSWORD@HOST:5432/DBNAME"
```

For Supabase, use the Supabase Postgres connection string and set `DATA_BACKEND=supabase`.

## LLM Provider

### Mock (default)

```bash
export LLM_PROVIDER=mock
```

### OpenAI

```bash
export LLM_PROVIDER=openai
export OPENAI_API_KEY="<your-key>"
export OPENAI_MODEL="gpt-4.1-mini"
```

### Gemini

```bash
export LLM_PROVIDER=gemini
export GEMINI_API_KEY="<your-key>"
export GEMINI_MODEL="gemini-2.0-flash"
```

## API

- `GET /api/health`
- `POST /api/cases/intake`
- `GET /api/cases`
- `GET /api/cases/{case_id}`
- `POST /api/cases/{case_id}/review`
- `GET /api/ev-support/health`
- `POST /api/ev-support/respond`
- `POST /api/ev-support/line/webhook`
- `GET /api/ev-support/logs`

### API Authentication

Case endpoints require `X-API-Key` when `API_AUTH_KEY` is set in env.

Example:

```bash
curl -H "X-API-Key: <API_AUTH_KEY>" http://127.0.0.1:8000/api/cases
```

### Google Slides Output

Module B can optionally publish the generated presentation to Google Slides.

Required env vars:

```bash
GOOGLE_SLIDES_ENABLED=true
GOOGLE_SERVICE_ACCOUNT_FILE=/absolute/path/to/service-account.json
GOOGLE_SLIDES_FOLDER_ID=<optional-drive-folder-id>
```

When configured, the returned presentation payload includes:

- `google_presentation_id`
- `google_presentation_url`

## n8n Integration

Import:

- `n8n/insurance_assistant_workflow.json`
- `n8n/ev_line_support_workflow.json`

Required n8n env var:

- `API_BASE_URL` (example: `http://host.docker.internal:8000` if n8n runs in Docker)

Workflow behavior:

1. Receives intake webhook
2. Calls your API MAIN intake endpoint
3. Checks compliance notes
4. Auto-approves or auto-holds case
5. Calls review endpoint
6. Returns final status from webhook

### LINE + Thai EV Support

The EV support chatbot supports two operating modes:

1. Direct mode: LINE sends webhook events to `/api/ev-support/line/webhook`, the app generates a Thai reply, and the app replies back to LINE when `LINE_CHANNEL_ACCESS_TOKEN` is configured.
2. n8n mode: LINE sends webhook events to `/api/ev-support/line/webhook`, the app forwards the normalized event to `EV_N8N_WEBHOOK_URL`, and n8n orchestrates LLM/retrieval/reply logic.

Suggested env vars:

```bash
LINE_CHANNEL_SECRET="<line-channel-secret>"
LINE_CHANNEL_ACCESS_TOKEN="<line-channel-access-token>"
EV_N8N_WEBHOOK_URL="https://<your-n8n-host>/webhook/ev-line-support"
EV_DEFAULT_LANGUAGE="en-US"
EV_ENABLE_THAI_AFTER_SETUP="false"
```

Default rollout behavior:

- Start in English with `EV_DEFAULT_LANGUAGE=en-US`
- Keep `EV_ENABLE_THAI_AFTER_SETUP=false` while webhook, LINE credentials, and n8n flow are still being tested
- Switch to Thai later by setting `EV_ENABLE_THAI_AFTER_SETUP=true`

Example local reply request:

```bash
curl -X POST http://127.0.0.1:8000/api/ev-support/respond \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "line:U123",
    "user_id": "U123",
    "message_text": "เสียบหัวชาร์จแล้ว แต่ยังไม่เริ่มชาร์จ",
    "channel": "line",
    "language": "th-TH",
    "context": {
      "station_id": "BKK-CHA-01",
      "charger_type": "DC Fast"
    }
  }'
```

### FAQ to RAG

The EV chatbot now supports a simple local FAQ-based retrieval flow.

- Source file: `data/ev_faq.json`
- Retriever: lexical top-k matching in `app/ev_support/faq_store.py`
- Runtime usage: `app/ev_support/service.py`
- CSV importer: `scripts/build_ev_faq_corpus.py`

Recommended authoring format for your on-hand FAQ:

```json
{
  "doc_id": "faq-en-refund",
  "language": "en-US",
  "question": "I was charged but charging did not start.",
  "answer": "Please share the station ID, incident time, and the last 4 digits of the payment reference so support can investigate.",
  "tags": ["refund", "payment", "billing", "failed charge"]
}
```

Use `EV_FAQ_PATH` if you want to point the app to another FAQ corpus file.
Conversation logging uses a local SQLite database path set by `EV_CHAT_LOG_DB_PATH`.
LINE image and audio attachments are stored locally under `EV_MEDIA_STORAGE_DIR`.
When `LLM_PROVIDER=openai`, screenshot analysis uses the Responses API and audio clips use `OPENAI_TRANSCRIBE_MODEL`.
Human escalation can notify a LINE support group via `LINE_SUPPORT_GROUP_ID` and pause bot handling for that session.

To convert the XECO voicebot CSV into the chatbot corpus:

```bash
python3 scripts/build_ev_faq_corpus.py \
  "/Users/thomasmok/Library/CloudStorage/Dropbox/Work/ET/Projects/AX/EV charging/FAQ/XECO_FAQ_voicebot_20260213a.csv" \
  data/xeco_faq_corpus.json
```

Then set:

```bash
EV_FAQ_PATH=/Users/thomasmok/Documents/Playground/data/xeco_faq_corpus.json
```

### Production Deployment

Recommended files for customer deployment:

- `Dockerfile`
- `render.yaml`
- `.env.production.example`
- `docs/EV_CHATBOT_ARCHITECTURE.md`
- `docs/CUSTOMER_HANDOVER_CHECKLIST.md`

Recommended deployment path:

1. Push the repo to GitHub
2. Deploy on Render using `render.yaml`
3. Set the production secrets in the Render dashboard
4. Update the LINE webhook URL to the production domain

## Permanent Deploy (Render)

This repo now includes:

- `Dockerfile`
- `render.yaml`

Deploy steps:

1. Push this repo to GitHub.
2. In Render, create a new Blueprint from this repo.
3. Set secret env vars in Render:
   - `DATABASE_URL` (Supabase)
   - `OPENAI_API_KEY`
4. Wait for deploy and copy your public URL (for example `https://ai-insurance-assistant.onrender.com`).

Then update n8n workflow HTTP nodes to use:

- `https://<your-render-url>/api/cases/intake`
- `https://<your-render-url>/api/cases/{{$json.case_id}}/review`

## Example Intake Request

```bash
curl -X POST http://127.0.0.1:8000/api/cases/intake \
  -H "Content-Type: application/json" \
  -d '{
    "input": {
      "client_profile": {
        "name_or_code": "Client A",
        "age": 40,
        "gender": "Male",
        "marital_status": "Married",
        "dependents": 2,
        "occupation": "Manager",
        "income_monthly": 50000,
        "expenses_monthly": 25000,
        "budget_monthly": 3000
      },
      "insurance_profile": {
        "existing_medical": true,
        "existing_ci": false,
        "existing_life": false,
        "existing_accident": true,
        "current_premium": 1200
      },
      "source": "web_form"
    },
    "generate_presentation": true,
    "generate_content": true
  }'
```
