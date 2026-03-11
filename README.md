# AI Insurance Sales Assistant (MVP+)

This app is built from your SRD and now includes:

- MAIN intake/orchestration workflow
- Module A/B/C generation flow
- Shared subflows (validate, normalize, compliance, notify, error)
- Case Data Layer with **memory** or **PostgreSQL/Supabase** backend
- Prompt registry layer
- LLM provider adapter (**Mock / OpenAI / Gemini**)
- n8n workflow JSON for end-to-end orchestration

## Structure

- `app/core/service.py`: MAIN_Case_Intake_And_Orchestration
- `app/core/bootstrap.py`: dependency wiring by env
- `app/core/config.py`: environment configuration
- `app/data/repository.py`: InMemory + Postgres/Supabase repository
- `app/llm/prompt_registry.py`: prompt registry loader
- `app/llm/providers.py`: provider clients (Mock/OpenAI/Gemini)
- `prompts/registry.json`: versioned prompt templates
- `n8n/insurance_assistant_workflow.json`: importable n8n workflow

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

Required n8n env var:

- `API_BASE_URL` (example: `http://host.docker.internal:8000` if n8n runs in Docker)

Workflow behavior:

1. Receives intake webhook
2. Calls your API MAIN intake endpoint
3. Checks compliance notes
4. Auto-approves or auto-holds case
5. Calls review endpoint
6. Returns final status from webhook

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
