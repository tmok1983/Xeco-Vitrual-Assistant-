## XECO EV Chatbot Architecture and SOP

### Purpose

This document describes the current XECO EV charging customer service chatbot setup, including:

- system architecture
- message workflow
- repository structure
- RAG design
- operating procedures
- known limitations
- recommended next steps

The current production-style setup is a LINE chatbot that receives customer messages, detects the input language, retrieves relevant FAQ content from the XECO knowledge base, and sends a reply back to LINE in the same language.

### Current Architecture

The active runtime path is:

`LINE -> FastAPI webhook -> language detection -> FAQ retrieval -> response generation -> LINE reply API`

The chatbot is currently running in direct reply mode. This means the FastAPI app receives the webhook from LINE and sends the reply back to LINE directly. The prepared n8n workflow exists for future orchestration, but it is not required in the live reply path right now.

### Message Workflow

1. A customer sends a message in LINE.
2. LINE sends a webhook event to `/api/ev-support/line/webhook`.
3. The app verifies the `X-Line-Signature` using `LINE_CHANNEL_SECRET`.
4. The app converts the webhook event into an internal EV support request.
5. The EV support service detects the message language.
6. The EV support service detects the likely support intent.
7. The EV support service retrieves matching FAQ entries from the local XECO corpus.
8. The service builds a response in the detected language.
9. The app calls the LINE reply API using `LINE_CHANNEL_ACCESS_TOKEN`.
10. The customer receives the reply in LINE.

### Language Behavior

The current language rules are:

- English input -> reply in English
- Chinese input -> reply in Traditional Chinese
- Mixed Chinese and English -> reply in Traditional Chinese
- Thai input -> reply in Thai

Language detection is handled in the EV support service based on the message text itself rather than relying only on upstream metadata.

### Repository Structure

Core files for the chatbot are:

- API entrypoint: [app/main.py](/Users/thomasmok/Documents/Playground/app/main.py)
- Webhook and support routes: [app/api/routes.py](/Users/thomasmok/Documents/Playground/app/api/routes.py)
- Configuration and environment loading: [app/core/config.py](/Users/thomasmok/Documents/Playground/app/core/config.py)
- Dependency wiring: [app/core/bootstrap.py](/Users/thomasmok/Documents/Playground/app/core/bootstrap.py)
- EV support logic: [app/ev_support/service.py](/Users/thomasmok/Documents/Playground/app/ev_support/service.py)
- FAQ retriever: [app/ev_support/faq_store.py](/Users/thomasmok/Documents/Playground/app/ev_support/faq_store.py)
- EV support models: [app/ev_support/models.py](/Users/thomasmok/Documents/Playground/app/ev_support/models.py)
- In-memory session store: [app/ev_support/repository.py](/Users/thomasmok/Documents/Playground/app/ev_support/repository.py)
- Generated FAQ corpus: [data/xeco_faq_corpus.json](/Users/thomasmok/Documents/Playground/data/xeco_faq_corpus.json)
- FAQ corpus builder: [scripts/build_ev_faq_corpus.py](/Users/thomasmok/Documents/Playground/scripts/build_ev_faq_corpus.py)
- n8n workflow template: [n8n/ev_line_support_workflow.json](/Users/thomasmok/Documents/Playground/n8n/ev_line_support_workflow.json)

### RAG Design

The current chatbot uses a lightweight local RAG design.

#### Knowledge source

The active FAQ corpus is:

- [data/xeco_faq_corpus.json](/Users/thomasmok/Documents/Playground/data/xeco_faq_corpus.json)

This corpus was generated from the XECO FAQ spreadsheet and currently contains:

- 25 English entries
- 25 Traditional Chinese entries
- 25 Thai entries

Each FAQ record includes:

- `doc_id`
- `language`
- `question`
- `answer`
- `tags`
- `category`
- `intent`

#### Retrieval

Retrieval is handled by:

- [app/ev_support/faq_store.py](/Users/thomasmok/Documents/Playground/app/ev_support/faq_store.py)

The retriever is lexical and uses weighted overlap against:

- FAQ question text
- FAQ answer text
- FAQ tags

Question matches are weighted more heavily than answer matches, and direct phrase matches are boosted.

#### Generation

Generation is handled by:

- [app/ev_support/service.py](/Users/thomasmok/Documents/Playground/app/ev_support/service.py)

The service:

- detects language
- detects intent
- retrieves FAQ hits
- uses the top FAQ answer as the primary response basis when available
- falls back to language-specific support templates when retrieval is weak

This is not an embedding-based vector RAG implementation yet. It is a deterministic FAQ retrieval system that is fast, easy to maintain, and suitable for an MVP and pilot stage.

### FAQ Source and Import Process

The current FAQ source with Thai content was provided as a Numbers file. Because `.numbers` files are not directly ingestible by the app, the operational flow is:

1. Export the Numbers file to `.xlsx`
2. Convert the `.xlsx` into the chatbot corpus JSON
3. Restart the API

The corpus builder supports `.csv` and `.xlsx` inputs.

Example build command:

```bash
python3 /Users/thomasmok/Documents/Playground/scripts/build_ev_faq_corpus.py \
  /tmp/XECO_FAQ_Chatbot_20260120_th_added.xlsx \
  /Users/thomasmok/Documents/Playground/data/xeco_faq_corpus.json
```

### Environment Configuration

Key chatbot environment variables are stored in:

- [.env](/Users/thomasmok/Documents/Playground/.env)

Important settings:

- `LINE_CHANNEL_SECRET`
- `LINE_CHANNEL_ACCESS_TOKEN`
- `LINE_REPLY_API_URL`
- `EV_FAQ_PATH`
- `EV_DEFAULT_LANGUAGE`
- `EV_ENABLE_THAI_AFTER_SETUP`

The active FAQ path should point to:

- [data/xeco_faq_corpus.json](/Users/thomasmok/Documents/Playground/data/xeco_faq_corpus.json)

### Standard Operating Procedure

#### SOP: Update FAQ knowledge base

1. Update the XECO FAQ source spreadsheet.
2. Export the file to `.xlsx` if the source is a Numbers document.
3. Run the corpus builder script.
4. Confirm that the generated corpus contains the expected language entries.
5. Restart the API service.
6. Test one English, one Chinese, and one Thai query in LINE.

#### SOP: Validate webhook health

1. Confirm the public webhook URL is still valid.
2. Confirm LINE webhook verification passes.
3. Confirm the app receives message events.
4. Confirm replies are returned to LINE successfully.
5. Review server logs if reply delivery fails.

#### SOP: Troubleshoot poor answers

1. Check whether the FAQ exists in the corpus.
2. Check whether the question wording is close enough to the user phrasing.
3. Review the FAQ tags and category.
4. Review retrieval ranking behavior.
5. Add or refine FAQ entries if the user question pattern is common.

### Operational Notes

- The live app must run with outbound network access so it can call the LINE reply API.
- The current session store is in-memory, which is acceptable for the current pilot but not ideal for long-term conversation state.
- The current RAG layer is local and file-based, which keeps deployment simple.
- The system can operate without n8n today.

### n8n Role

The repository includes:

- [n8n/ev_line_support_workflow.json](/Users/thomasmok/Documents/Playground/n8n/ev_line_support_workflow.json)

n8n is prepared for future use cases such as:

- human escalation
- CRM or ticket creation
- refund workflows
- station outage handling
- approval or review flows
- operational logging and notification routing

At present, the simplest and most stable live path is still direct reply mode:

`LINE -> app -> LINE`

### Known Limitations

- Retrieval is lexical rather than semantic.
- Session memory is not persisted beyond the in-memory store.
- No live backend integration is in place yet for wallet, station status, charging session lookup, or refund status.
- No structured analytics or dashboard is included yet.
- Some user phrasings may still retrieve a nearby FAQ instead of the ideal one.

### Recommended Next Steps

Recommended order of improvement:

1. Improve retrieval ranking with stronger intent-aware boosts.
2. Add explicit FAQ-to-intent mapping based on the spreadsheet `Intent` column.
3. Add operational backend integrations for live station and payment support.
4. Add conversation logging and reporting.
5. Introduce vector search only if lexical retrieval becomes a quality bottleneck.
6. Add n8n orchestration after the direct support path is stable enough.

### Summary

The current XECO chatbot is a multilingual LINE support bot backed by a local FAQ-based RAG layer. It is simple, practical, and maintainable for pilot use. The most important design choice so far is keeping the live path straightforward while making the FAQ corpus easy to update and re-ingest as the support content evolves.
