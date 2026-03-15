## XECO EV Chatbot Customer Handover Checklist

### Deployment

- Production service is deployed and reachable over HTTPS
- Health endpoint returns success:
  - `/api/ev-support/health`
- FAQ corpus is present in deployment:
  - `/app/data/xeco_faq_corpus.json`
- Production environment variables are configured
- Render runtime disk is configured for persistent pilot data:
  - `/app/runtime/chat_logs.db`
  - `/app/runtime/line_media`
- LINE webhook URL points to production:
  - `/api/ev-support/line/webhook`

### LINE Access

- Customer has owner or admin access to the LINE Official Account
- Customer has access to the LINE Developers Console
- Customer has the current:
  - Channel secret
  - Channel access token
- Webhook is enabled
- Conflicting auto-reply behavior is disabled in Official Account Manager

### Knowledge Base

- Current master FAQ source file is identified
- Team knows how to export the FAQ source to `.xlsx`
- Team knows how to rebuild the corpus:

```bash
python3 /Users/thomasmok/Documents/Playground/scripts/build_ev_faq_corpus.py \
  /path/to/exported_faq.xlsx \
  /Users/thomasmok/Documents/Playground/data/xeco_faq_corpus.json
```

- Team knows a deploy or restart is required after corpus updates

### Functional Acceptance Tests

- English query returns English reply
- Chinese query returns Chinese reply
- Mixed Chinese + English returns Chinese reply
- Thai query returns Thai reply
- Refund/payment issue returns refund-oriented guidance
- Charging issue returns troubleshooting guidance
- Webhook verification succeeds in LINE

### Operations SOP

- Team knows where runtime logs are stored or viewed
- Team knows how to restart the service
- Team knows how to rotate LINE credentials
- Team knows how to rotate OpenAI credentials
- Team has an escalation contact for production issues

### Known Limitations

- Retrieval is lexical rather than vector/semantic
- Session memory is in-memory only
- No CRM/ticketing workflow is live yet
- No live station-status or refund-status integration yet
- FAQ updates require rebuild plus redeploy/restart

### Recommended Ownership Split

- Customer team:
  - Owns FAQ content
  - Owns LINE Official Account
  - Performs user acceptance testing

- Delivery team:
  - Owns app deployment
  - Owns code changes
  - Owns retriever tuning
  - Owns incident support during pilot
