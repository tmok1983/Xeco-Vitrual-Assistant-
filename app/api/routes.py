from __future__ import annotations

import json
import ssl
from urllib import request
from urllib.error import URLError

import certifi
from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from app.core.config import AppConfig
from app.core.bootstrap import build_service
from app.core.models import IntakeRequest, ReviewDecision
from app.modules.google_slides import build_google_oauth_url, get_google_oauth_status, handle_google_oauth_callback
from app.modules.proposal_pdf import build_proposal_pdf

router = APIRouter(prefix="/api", tags=["insurance-assistant"])
service = build_service()
config = AppConfig.from_env()


def require_api_key(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    key: str | None = Query(default=None),
) -> None:
    if not config.api_auth_key:
        return
    candidate = x_api_key or key
    if candidate != config.api_auth_key:
        raise HTTPException(status_code=401, detail="Invalid API key")


def _post_to_n8n_webhook(payload: dict) -> tuple[int, dict]:
    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    req = request.Request(
        config.n8n_webhook_url,
        method="POST",
        headers={"Content-Type": "application/json"},
        data=json.dumps(payload).encode("utf-8"),
    )
    with request.urlopen(req, timeout=120, context=ssl_ctx) as resp:  # nosec B310
        raw = resp.read().decode("utf-8")
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            body = {"raw": raw}
        return resp.status, body


def _format_case_output(case) -> dict:
    analysis = case.analysis_result
    risk = analysis.risk_gap if analysis else None
    presentation = case.presentation_result
    compliance = case.compliance_report
    return {
        "case_id": case.case_id,
        "status": case.status.value,
        "review_status": case.review_status.value,
        "module_a_report": {
            "summary": analysis.summary if analysis else None,
            "technical_summary": analysis.technical_summary if analysis else None,
            "advisor_narrative": analysis.advisor_narrative if analysis else None,
            "recommendations": analysis.recommendations if analysis else [],
            "risk_score": risk.score if risk else None,
            "risk_ranking": risk.ranking if risk else [],
            "risk_findings": risk.findings if risk else [],
        },
        "compliance_report": {
            "risk_level": compliance.risk_level.value if compliance else None,
            "approval_recommendation": compliance.approval_recommendation if compliance else None,
            "required_disclosures": compliance.required_disclosures if compliance else [],
            "issues": [issue.model_dump(mode="json") for issue in compliance.issues] if compliance else [],
            "safe_summary": compliance.safe_summary if compliance else None,
        },
        "presentation": {
            "page_count": len(presentation.pages) if presentation and presentation.pages else 0,
            "pages": presentation.pages if presentation else [],
            "google_presentation_id": presentation.google_presentation_id if presentation else None,
            "google_presentation_url": presentation.google_presentation_url if presentation else None,
        },
        "raw_case": case.model_dump(mode="json"),
    }


def _run_local_workbench(req: IntakeRequest) -> tuple[int, dict]:
    result = service.intake_and_orchestrate(req)
    if "error" in result:
        err = result["error"]
        return 502, {
            "dead_letter": True,
            "status": "failed",
            "stage": "local_backend",
            "error": err.model_dump(mode="json"),
        }

    case = result["case"]
    compliance = case.compliance_report
    risk_level = compliance.risk_level.value if compliance else "low"
    approved = risk_level != "high"
    reason = (
        f"Manual review required (high risk): {'; '.join(f'{i.claim}:{i.reason}' for i in compliance.issues) or 'high risk flagged'}"
        if not approved
        else (
            f"Approved with disclosures: {' | '.join(compliance.required_disclosures)}"
            if risk_level == "medium"
            else "Auto-approved: low compliance risk"
        )
    )
    decision = ReviewDecision(
        approved=approved,
        reviewer="local-compliance-bot",
        reason=reason,
    )
    reviewed_case = service.review_case(case.case_id, decision)
    payload = _format_case_output(reviewed_case)
    payload["execution_mode"] = "local_fallback"
    payload["notification"] = result["notification"]
    return 200, payload


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/google/status")
def google_status() -> dict:
    return get_google_oauth_status(config)


@router.get("/google/start")
def google_start() -> RedirectResponse:
    auth_url = build_google_oauth_url(config)
    if not auth_url:
        raise HTTPException(status_code=400, detail="Google OAuth is not configured")
    return RedirectResponse(auth_url)


@router.get("/google/callback", response_class=HTMLResponse)
def google_callback(code: str, state: str | None = None) -> HTMLResponse:
    try:
        handle_google_oauth_callback(config, code=code, state=state)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return HTMLResponse(
        """
<!doctype html>
<html>
<head><meta charset="utf-8"><title>Google 已連接</title></head>
<body style="font-family: Arial, sans-serif; max-width: 560px; margin: 40px auto; color: #111827;">
  <h2>Google Slides 已連接</h2>
  <p>你可以關閉這個視窗，回到工作台繼續生成簡報。</p>
  <script>
    setTimeout(() => window.close(), 1200);
  </script>
</body>
</html>
"""
    )


@router.post("/cases/intake", dependencies=[Depends(require_api_key)])
def intake(req: IntakeRequest) -> dict:
    result = service.intake_and_orchestrate(req)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"].model_dump(mode="json"))
    return {
        "case_id": result["case"].case_id,
        "status": result["case"].status,
        "review_status": result["case"].review_status,
        "notification": result["notification"],
        "case": result["case"],
    }


@router.get("/cases", dependencies=[Depends(require_api_key)])
def list_cases() -> list[dict]:
    return [c.model_dump() for c in service.repo.list_all()]


@router.get("/cases/{case_id}", dependencies=[Depends(require_api_key)])
def get_case(case_id: str) -> dict:
    case = service.repo.get(case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    return case.model_dump()


@router.get("/cases/{case_id}/presentation", response_class=HTMLResponse, dependencies=[Depends(require_api_key)])
def get_case_presentation(case_id: str) -> HTMLResponse:
    case = service.repo.get(case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    if not case.presentation_result or not case.presentation_result.pages:
        raise HTTPException(status_code=404, detail="Presentation not found for this case")

    slides_html = []
    for i, page in enumerate(case.presentation_result.pages, start=1):
        title = page.get("title", f"Slide {i}")
        section = page.get("section", "")
        key_message = page.get("key_message", "")
        supporting_points = page.get("supporting_points", "")
        advisor_recommendation = page.get("advisor_recommendation", "")
        visual_icon = page.get("visual_icon", "📘")
        content = page.get("content", "")
        slides_html.append(
            f"""
            <section class="slide">
              <div class="slide-no">Slide {i}</div>
              <div class="slide-section">{section}</div>
              <h2>{title}</h2>
              <div class="slide-key">{key_message}</div>
              <div class="slide-grid">
                <div>
                  <h3>分析重點</h3>
                  <p>{supporting_points or content}</p>
                </div>
                <div>
                  <h3>顧問建議</h3>
                  <p>{advisor_recommendation or "請依客戶需求進一步調整建議節奏。"}</p>
                </div>
              </div>
              <div class="slide-footer">
                <div class="footer-meta">ET Consulting | Insurance Advisory Proposal</div>
                <div class="slide-visual">
                  <div class="visual-icon">{visual_icon}</div>
                </div>
              </div>
            </section>
            """
        )

    html = f"""
<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>簡報檢視 - {case.case_id}</title>
  <style>
    :root {{
      --font-cjk: "PingFang TC", "PingFang HK", "Hiragino Sans GB", "Microsoft JhengHei", "Noto Sans CJK TC", "Source Han Sans TC", sans-serif;
      --font-latin: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
    }}
    body {{
      margin: 0;
      font-family: var(--font-cjk), var(--font-latin);
      background: #f3f4f6;
      color: #111827;
      -webkit-font-smoothing: antialiased;
      text-rendering: optimizeLegibility;
    }}
    .wrap {{
      max-width: 980px;
      margin: 24px auto;
      padding: 0 16px 32px;
    }}
    .header {{
      margin-bottom: 16px;
      padding: 16px;
      background: white;
      border: 1px solid #e5e7eb;
      border-radius: 12px;
    }}
    .actions {{
      margin-top: 12px;
    }}
    .btn {{
      display: inline-block;
      border: 1px solid #d1d5db;
      background: #111827;
      color: #fff;
      border-radius: 8px;
      padding: 8px 12px;
      font-size: 14px;
      cursor: pointer;
      text-decoration: none;
      margin-right: 8px;
    }}
    .btn-secondary {{
      background: #fff;
      color: #111827;
    }}
    .header h1 {{
      margin: 0 0 8px;
      font-size: 22px;
      font-family: var(--font-cjk), var(--font-latin);
    }}
    .meta {{
      color: #4b5563;
      font-size: 14px;
    }}
    .slide {{
      background: white;
      border: 1px solid #e5e7eb;
      border-radius: 12px;
      padding: 24px;
      margin-top: 16px;
      min-height: 280px;
      box-shadow: 0 4px 16px rgba(17, 24, 39, 0.06);
    }}
    .slide-no {{
      color: #6b7280;
      font-size: 12px;
      margin-bottom: 10px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }}
    .slide-section {{
      display: inline-block;
      margin-bottom: 10px;
      padding: 4px 10px;
      border-radius: 999px;
      background: rgba(180, 83, 9, 0.12);
      color: #b45309;
      font-size: 12px;
      letter-spacing: 0.08em;
    }}
    .slide h2 {{
      margin: 0 0 12px;
      font-size: 28px;
      font-family: var(--font-cjk), var(--font-latin);
    }}
    .slide-key {{
      margin-bottom: 16px;
      font-size: 20px;
      font-weight: 700;
      color: #1f2937;
      line-height: 1.5;
      font-family: var(--font-cjk), var(--font-latin);
    }}
    .slide-grid {{
      display: grid;
      grid-template-columns: 1.1fr 0.9fr;
      gap: 18px;
    }}
    .slide-footer {{
      margin-top: 20px;
      display: flex;
      align-items: center;
      justify-content: space-between;
    }}
    .slide-visual {{
      width: 72px;
      min-height: 56px;
      display: flex;
      align-items: center;
      justify-content: center;
    }}
    .visual-icon {{
      font-size: 40px;
      line-height: 1;
      color: #b45309;
      font-family: "Apple Color Emoji", "Segoe UI Emoji", "Noto Color Emoji", sans-serif;
    }}
    .footer-meta {{
      font-size: 12px;
      color: #6b7280;
      letter-spacing: 0.04em;
    }}
    .slide h3 {{
      margin: 0 0 8px;
      font-size: 12px;
      letter-spacing: 0.1em;
      text-transform: uppercase;
      color: #6b7280;
    }}
    .slide p {{
      font-size: 20px;
      line-height: 1.5;
      white-space: pre-wrap;
      font-family: var(--font-cjk), var(--font-latin);
    }}
    @media print {{
      @page {{
        size: A4 landscape;
        margin: 10mm;
      }}
      body {{
        background: #fff;
        -webkit-print-color-adjust: exact;
        print-color-adjust: exact;
      }}
      .header {{
        border: none;
        padding: 0 0 8px;
      }}
      .actions {{
        display: none;
      }}
      .slide {{
        box-shadow: none;
        page-break-after: always;
        min-height: 0;
      }}
      .slide-grid {{
        grid-template-columns: 1fr;
      }}
      .slide:last-child {{
        page-break-after: auto;
      }}
    }}
  </style>
</head>
<body>
  <main class="wrap">
    <header class="header">
      <h1>客戶簡報檢視</h1>
      <div class="meta">
        案件編號: <strong>{case.case_id}</strong> |
        案件狀態: <strong>{case.status.value}</strong> |
        審核狀態: <strong>{case.review_status.value}</strong>
      </div>
      <div class="actions">
        <a class="btn" href="/api/cases/{case.case_id}/proposal.pdf?key={config.api_auth_key}" target="_blank" rel="noopener">下載 PDF</a>
        <button class="btn btn-secondary" onclick="window.print()">瀏覽器列印</button>
      </div>
    </header>
    {''.join(slides_html)}
  </main>
</body>
</html>
"""
    return HTMLResponse(content=html)


@router.get("/cases/{case_id}/proposal.pdf", dependencies=[Depends(require_api_key)])
def get_case_proposal_pdf(case_id: str) -> Response:
    case = service.repo.get(case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    if not case.presentation_result or not case.presentation_result.pages:
        raise HTTPException(status_code=404, detail="Presentation not found for this case")

    try:
        pdf_bytes = build_proposal_pdf(case)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    headers = {
        "Content-Disposition": f'inline; filename="proposal-{case.case_id}.pdf"',
    }
    return Response(content=pdf_bytes, media_type="application/pdf", headers=headers)


@router.post("/cases/{case_id}/review", dependencies=[Depends(require_api_key)])
def review_case(case_id: str, decision: ReviewDecision) -> dict:
    try:
        case = service.review_case(case_id, decision)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return {
        "case_id": case.case_id,
        "status": case.status,
        "review_status": case.review_status,
        "events": case.events,
    }


@router.get("/test-form", response_class=HTMLResponse)
def test_form() -> str:
    return """
<!doctype html>
<html>
<head><meta charset="utf-8"><title>Insurance Workflow Test Form v2</title></head>
<body style="font-family: Arial, sans-serif; max-width: 720px; margin: 24px auto;">
  <h2>Insurance Workflow Test Form v2</h2>
  <p>Submit directly to n8n webhook: <code>insurance-intake-codex-v3</code></p>
  <form id="f">
    <h3>Client Profile</h3>
    <label>Name / Client Code<br/><input name="name_or_code" placeholder="Name / Code" value="Client Test" required /></label><br/><br/>
    <label>Age<br/><input name="age" type="number" placeholder="Age" value="35" required /></label><br/><br/>
    <label>Gender<br/><select name="gender"><option>Male</option><option>Female</option><option>Other</option></select></label><br/><br/>
    <label>Marital Status<br/><select name="marital_status"><option>Single</option><option>Married</option><option>Divorced</option><option>Widowed</option></select></label><br/><br/>
    <label>Number of Dependents<br/><input name="dependents" type="number" value="1" required /></label><br/><br/>
    <label>Occupation<br/><input name="occupation" value="Manager" required /></label><br/><br/>
    <label>Monthly Salary / Income<br/><input name="income_monthly" type="number" value="50000" required /></label><br/><br/>
    <label>Monthly Expenses<br/><input name="expenses_monthly" type="number" value="25000" required /></label><br/><br/>
    <label>Monthly Insurance Budget<br/><input name="budget_monthly" type="number" value="3000" required /></label><br/><br/>

    <h3>Insurance Profile</h3>
    <label><input type="checkbox" name="existing_medical" checked /> Existing Medical</label><br/>
    <label><input type="checkbox" name="existing_ci" /> Existing CI</label><br/>
    <label><input type="checkbox" name="existing_life" /> Existing Life</label><br/>
    <label><input type="checkbox" name="existing_accident" checked /> Existing Accident</label><br/><br/>
    <label>Current Monthly Premium<br/><input name="current_premium" type="number" value="1200" required /></label><br/><br/>

    <button type="submit">Submit to n8n</button>
  </form>
  <pre id="out" style="background:#f6f8fa; padding:12px; margin-top:16px;"></pre>
  <script>
    const submitUrl = "/api/test-form/submit";
    document.getElementById("f").addEventListener("submit", async (e) => {
      e.preventDefault();
      const fd = new FormData(e.target);
      const payload = {
        input: {
          client_profile: {
            name_or_code: fd.get("name_or_code"),
            age: Number(fd.get("age")),
            gender: fd.get("gender"),
            marital_status: fd.get("marital_status"),
            dependents: Number(fd.get("dependents")),
            occupation: fd.get("occupation"),
            income_monthly: Number(fd.get("income_monthly")),
            expenses_monthly: Number(fd.get("expenses_monthly")),
            budget_monthly: Number(fd.get("budget_monthly"))
          },
          insurance_profile: {
            existing_medical: fd.get("existing_medical") === "on",
            existing_ci: fd.get("existing_ci") === "on",
            existing_life: fd.get("existing_life") === "on",
            existing_accident: fd.get("existing_accident") === "on",
            current_premium: Number(fd.get("current_premium"))
          },
          source: "web_form"
        },
        generate_presentation: true,
        generate_content: true
      };
      const res = await fetch(submitUrl, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(payload)
      });
      const text = await res.text();
      document.getElementById("out").textContent = `HTTP ${res.status}\\n${text}`;
    });
  </script>
</body>
</html>
"""


@router.post("/test-form/submit")
def test_form_submit(payload: dict = Body(...)) -> JSONResponse:
    try:
        status_code, body = _post_to_n8n_webhook(payload)
        return JSONResponse(status_code=status_code, content=body)
    except Exception as exc:
        return JSONResponse(status_code=502, content={"error": str(exc)})


@router.get("/workbench", response_class=HTMLResponse)
def workbench() -> str:
    return """
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>保險案件工作台</title>
  <style>
    :root {
      --bg: #f5efe4;
      --ink: #1f2937;
      --muted: #6b7280;
      --panel: rgba(255, 252, 247, 0.88);
      --line: rgba(80, 60, 32, 0.12);
      --accent: #b45309;
      --accent-2: #0f766e;
      --danger: #b91c1c;
      --shadow: 0 16px 40px rgba(64, 41, 10, 0.12);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(180, 83, 9, 0.12), transparent 32%),
        radial-gradient(circle at bottom right, rgba(15, 118, 110, 0.14), transparent 30%),
        linear-gradient(180deg, #fbf6ee 0%, #f1e6d3 100%);
      font-family: Georgia, "Times New Roman", serif;
    }
    .page {
      max-width: 1380px;
      margin: 0 auto;
      padding: 28px 18px 60px;
    }
    .hero {
      display: grid;
      grid-template-columns: 1.1fr 0.9fr;
      gap: 18px;
      margin-bottom: 18px;
    }
    .hero-card, .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 24px;
      box-shadow: var(--shadow);
      backdrop-filter: blur(10px);
    }
    .hero-copy {
      padding: 28px;
    }
    .eyebrow {
      display: inline-block;
      padding: 6px 10px;
      border-radius: 999px;
      background: rgba(180, 83, 9, 0.12);
      color: var(--accent);
      font-size: 12px;
      letter-spacing: 0.14em;
      text-transform: uppercase;
    }
    h1 {
      margin: 14px 0 10px;
      font-size: clamp(34px, 5vw, 62px);
      line-height: 0.95;
      font-weight: 700;
    }
    .hero-copy p {
      margin: 0;
      max-width: 52ch;
      color: #4b5563;
      font-size: 18px;
      line-height: 1.55;
    }
    .hero-stats {
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: 12px;
      padding: 18px;
    }
    .stat {
      padding: 18px;
      border-radius: 18px;
      background: rgba(255,255,255,0.7);
      border: 1px solid var(--line);
    }
    .stat-label {
      font-size: 12px;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: var(--muted);
    }
    .stat-value {
      margin-top: 10px;
      font-size: 28px;
      font-weight: 700;
    }
    .layout {
      display: grid;
      grid-template-columns: 420px 1fr;
      gap: 18px;
      align-items: start;
    }
    .panel {
      padding: 18px;
    }
    .panel h2, .panel h3 {
      margin: 0 0 14px;
      font-size: 22px;
    }
    .panel h3 {
      margin-top: 10px;
      font-size: 14px;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: var(--muted);
    }
    .grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 12px;
    }
    label {
      display: block;
      font-size: 13px;
      color: #374151;
      margin-bottom: 6px;
    }
    input, select {
      width: 100%;
      border-radius: 14px;
      border: 1px solid rgba(107, 114, 128, 0.25);
      padding: 12px 14px;
      font-size: 15px;
      background: rgba(255,255,255,0.9);
      color: var(--ink);
    }
    .checks {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
      margin-top: 10px;
    }
    .check {
      display: flex;
      gap: 10px;
      align-items: center;
      padding: 10px 12px;
      border-radius: 14px;
      border: 1px solid var(--line);
      background: rgba(255,255,255,0.7);
    }
    .check input {
      width: auto;
      margin: 0;
    }
    .actions {
      display: flex;
      gap: 10px;
      margin-top: 16px;
      flex-wrap: wrap;
    }
    button {
      border: 0;
      border-radius: 999px;
      padding: 12px 18px;
      font-size: 14px;
      font-weight: 700;
      cursor: pointer;
    }
    .primary {
      background: linear-gradient(135deg, #b45309, #ea580c);
      color: white;
    }
    .secondary {
      background: rgba(255,255,255,0.85);
      color: var(--ink);
      border: 1px solid var(--line);
    }
    .results {
      display: grid;
      gap: 18px;
    }
    .result-grid {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 12px;
    }
    .result-card {
      padding: 16px;
      border-radius: 18px;
      border: 1px solid var(--line);
      background: rgba(255,255,255,0.78);
    }
    .result-card strong {
      display: block;
      font-size: 12px;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.12em;
      margin-bottom: 8px;
    }
    .result-card span {
      font-size: 24px;
      font-weight: 700;
    }
    .split {
      display: grid;
      grid-template-columns: 1.15fr 0.85fr;
      gap: 18px;
    }
    .callout {
      padding: 16px;
      border-radius: 18px;
      border: 1px solid var(--line);
      background: rgba(255,255,255,0.76);
    }
    .callout p, .callout li {
      color: #374151;
      line-height: 1.6;
      margin: 0;
      white-space: pre-wrap;
    }
    .callout ul {
      margin: 0;
      padding-left: 18px;
    }
    .slides {
      display: grid;
      gap: 12px;
    }
    .slide {
      border-radius: 18px;
      padding: 18px;
      background: linear-gradient(180deg, rgba(255,255,255,0.95), rgba(250,245,236,0.95));
      border: 1px solid var(--line);
    }
    .slide-head {
      font-size: 11px;
      color: var(--muted);
      letter-spacing: 0.14em;
      text-transform: uppercase;
    }
    .slide h4 {
      margin: 8px 0 10px;
      font-size: 24px;
    }
    .slide .slide-section {
      display: inline-block;
      margin-top: 8px;
      padding: 4px 10px;
      border-radius: 999px;
      background: rgba(180, 83, 9, 0.12);
      color: #b45309;
      font-size: 11px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }
    .slide .slide-key {
      margin: 8px 0 12px;
      font-size: 18px;
      font-weight: 700;
      line-height: 1.5;
    }
    .slide .slide-grid {
      display: grid;
      grid-template-columns: 1.1fr 0.9fr;
      gap: 14px;
    }
    .slide .slide-footer {
      margin-top: 14px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
    }
    .slide .slide-visual {
      width: 68px;
      min-height: 52px;
      display: flex;
      align-items: center;
      justify-content: center;
    }
    .slide .visual-icon {
      font-size: 34px;
      line-height: 1;
      color: #b45309;
      font-family: "Apple Color Emoji", "Segoe UI Emoji", "Noto Color Emoji", sans-serif;
    }
    .slide .footer-meta {
      font-size: 11px;
      color: var(--muted);
      letter-spacing: 0.04em;
    }
    .slide .slide-block {
      padding: 12px;
      border-radius: 14px;
      background: rgba(255,255,255,0.75);
      border: 1px solid var(--line);
    }
    .slide .slide-block h5 {
      margin: 0 0 8px;
      font-size: 11px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--muted);
    }
    .slide p {
      margin: 0;
      font-size: 17px;
      line-height: 1.55;
      white-space: pre-wrap;
    }
    .status {
      padding: 12px 14px;
      border-radius: 14px;
      background: rgba(255,255,255,0.82);
      border: 1px solid var(--line);
      min-height: 48px;
      color: #374151;
      white-space: pre-wrap;
    }
    .linkbar {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      margin-top: 12px;
    }
    .linkbar a {
      color: var(--accent-2);
      text-decoration: none;
      font-weight: 700;
    }
    .danger {
      color: var(--danger);
    }
    @media (max-width: 1080px) {
      .hero, .layout, .split, .result-grid {
        grid-template-columns: 1fr;
      }
    }
  </style>
</head>
<body>
  <main class="page">
    <section class="hero">
      <div class="hero-card hero-copy">
        <span class="eyebrow">保險顧問應用</span>
        <h1>保險案件工作台</h1>
        <p>輸入客戶資料後，直接查看分析結果、合規建議與簡報內容。</p>
      </div>
      <div class="hero-card hero-stats">
        <div class="stat"><div class="stat-label">Module A</div><div class="stat-value">分析</div></div>
        <div class="stat"><div class="stat-label">Module B</div><div class="stat-value">簡報</div></div>
      </div>
    </section>

    <section class="layout">
      <div class="panel">
        <h2>案件輸入</h2>
        <div class="status" id="statusBox">系統已就緒。提交案件後會生成 Module A 與 Module B 輸出。</div>
        <form id="workbenchForm">
          <h3>客戶資料</h3>
          <div class="grid">
            <div><label>客戶名稱 / 編號</label><input name="name_or_code" value="Client Workbench" required /></div>
            <div><label>年齡</label><input name="age" type="number" value="39" required /></div>
            <div><label>性別</label><select name="gender"><option>Male</option><option selected>Female</option><option>Other</option></select></div>
            <div><label>婚姻狀況</label><select name="marital_status"><option>Single</option><option selected>Married</option><option>Divorced</option><option>Widowed</option></select></div>
            <div><label>受扶養人數</label><input name="dependents" type="number" value="1" required /></div>
            <div><label>職業</label><input name="occupation" value="Product Manager" required /></div>
            <div><label>每月收入</label><input name="income_monthly" type="number" value="82000" required /></div>
            <div><label>每月支出</label><input name="expenses_monthly" type="number" value="36000" required /></div>
            <div><label>每月保險預算</label><input name="budget_monthly" type="number" value="4500" required /></div>
            <div><label>目前每月保費</label><input name="current_premium" type="number" value="1400" required /></div>
          </div>

          <h3>現有保障</h3>
          <div class="checks">
            <label class="check"><input type="checkbox" name="existing_medical" checked /> 已有醫療保障</label>
            <label class="check"><input type="checkbox" name="existing_ci" /> 已有重大疾病保障</label>
            <label class="check"><input type="checkbox" name="existing_life" /> 已有壽險保障</label>
            <label class="check"><input type="checkbox" name="existing_accident" checked /> 已有意外保障</label>
          </div>

          <div class="actions">
            <button class="primary" type="submit">生成案件</button>
          </div>
          <p style="margin:12px 0 0; color:#6b7280; font-size:14px;">
            若系統已完成 Google Slides 設定，生成後會自動建立可分享的簡報連結。
          </p>
        </form>
      </div>

      <div class="results">
        <div class="panel">
          <h2>總覽</h2>
          <div class="result-grid">
            <div class="result-card"><strong>案件編號</strong><span id="caseId">-</span></div>
            <div class="result-card"><strong>案件狀態</strong><span id="caseStatus">-</span></div>
            <div class="result-card"><strong>審核狀態</strong><span id="reviewStatus">-</span></div>
            <div class="result-card"><strong>風險分數</strong><span id="riskScore">-</span></div>
            <div class="result-card"><strong>合規風險</strong><span id="complianceRisk">-</span></div>
            <div class="result-card"><strong>簡報頁數</strong><span id="slideCount">-</span></div>
            <div class="result-card"><strong>Google Slides</strong><span id="googleSlidesStatus">-</span></div>
          </div>
          <div class="linkbar" id="linkBar"></div>
          <div class="linkbar" id="googleAuthBar" style="margin-top:8px;"></div>
        </div>

        <div class="split">
          <div class="panel">
            <h2>Module A</h2>
            <div class="callout">
              <strong>技術摘要</strong>
              <p id="technicalSummary">尚未產生輸出。</p>
            </div>
            <div class="callout" style="margin-top:12px;">
              <strong>顧問說明</strong>
              <p id="advisorNarrative">尚未產生輸出。</p>
            </div>
            <div class="callout" style="margin-top:12px;">
              <strong>建議事項</strong>
              <ul id="recommendations"><li>尚未產生輸出。</li></ul>
            </div>
          </div>

          <div class="panel">
            <h2>合規結果</h2>
            <div class="callout">
              <strong>審核建議</strong>
              <p id="approvalRecommendation">尚未產生輸出。</p>
            </div>
            <div class="callout" style="margin-top:12px;">
              <strong>必要揭露</strong>
              <ul id="disclosures"><li>尚未產生輸出。</li></ul>
            </div>
            <div class="callout" style="margin-top:12px;">
              <strong>風險議題</strong>
              <ul id="issues"><li>尚未產生輸出。</li></ul>
            </div>
          </div>
        </div>

        <div class="panel">
          <h2>Module B</h2>
          <div class="slides" id="slides">
            <div class="slide">
              <div class="slide-head">簡報</div>
              <h4>等待生成</h4>
              <p>提交案件後，這裡會顯示 Module B 的簡報頁面內容。</p>
            </div>
          </div>
        </div>
      </div>
    </section>
  </main>

  <script>
    const form = document.getElementById("workbenchForm");
    const statusBox = document.getElementById("statusBox");
    const linkBar = document.getElementById("linkBar");
    const googleAuthBar = document.getElementById("googleAuthBar");

    async function loadGoogleStatus() {
      googleAuthBar.innerHTML = "";
      try {
        const response = await fetch("/api/google/status");
        const data = await response.json();
        const badge = document.createElement("span");
        badge.textContent = data.connected
          ? "Google Slides 已連接"
          : (data.configured ? "Google Slides 未連接" : "Google OAuth 尚未設定");
        googleAuthBar.appendChild(badge);

        if (data.configured && !data.connected) {
          const connect = document.createElement("a");
          connect.href = "/api/google/start";
          connect.target = "_blank";
          connect.textContent = "連接 Google 帳號";
          googleAuthBar.appendChild(connect);
        }
      } catch (error) {
        const badge = document.createElement("span");
        badge.textContent = "無法取得 Google 連線狀態";
        googleAuthBar.appendChild(badge);
      }
    }

    function setStatus(text, isError = false) {
      statusBox.textContent = text;
      statusBox.classList.toggle("danger", isError);
    }

    function setList(id, items, formatter) {
      const el = document.getElementById(id);
      el.innerHTML = "";
      const values = items && items.length ? items : ["尚未產生輸出。"];
      values.forEach((item) => {
        const li = document.createElement("li");
        li.textContent = formatter ? formatter(item) : item;
        el.appendChild(li);
      });
    }

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      setStatus("系統正在生成報告與簡報...");
      linkBar.innerHTML = "";

      const fd = new FormData(form);
      const payload = {
        input: {
          client_profile: {
            name_or_code: fd.get("name_or_code"),
            age: Number(fd.get("age")),
            gender: fd.get("gender"),
            marital_status: fd.get("marital_status"),
            dependents: Number(fd.get("dependents")),
            occupation: fd.get("occupation"),
            income_monthly: Number(fd.get("income_monthly")),
            expenses_monthly: Number(fd.get("expenses_monthly")),
            budget_monthly: Number(fd.get("budget_monthly"))
          },
          insurance_profile: {
            existing_medical: fd.get("existing_medical") === "on",
            existing_ci: fd.get("existing_ci") === "on",
            existing_life: fd.get("existing_life") === "on",
            existing_accident: fd.get("existing_accident") === "on",
            current_premium: Number(fd.get("current_premium"))
          },
          source: "workbench"
        },
        generate_presentation: true,
        generate_content: true
      };

      try {
        const response = await fetch("/api/workbench/generate", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        });
        const data = await response.json();
        if (!response.ok) {
          throw new Error(data.detail || data.error || "生成失敗");
        }

        if (data.dead_letter) {
          document.getElementById("caseId").textContent = "-";
          document.getElementById("caseStatus").textContent = data.status || "failed";
          document.getElementById("reviewStatus").textContent = "-";
          document.getElementById("riskScore").textContent = "-";
          document.getElementById("complianceRisk").textContent = "-";
          document.getElementById("slideCount").textContent = "-";
          document.getElementById("googleSlidesStatus").textContent = "-";
          document.getElementById("technicalSummary").textContent = "目前沒有分析輸出。";
          document.getElementById("advisorNarrative").textContent = "目前沒有顧問說明。";
          document.getElementById("approvalRecommendation").textContent = "流程失敗";
          setList("recommendations", []);
          setList("disclosures", []);
          setList("issues", [`${data.stage || "workflow"}: ${JSON.stringify(data.error || {})}`]);
          document.getElementById("slides").innerHTML = '<div class="slide"><div class="slide-head">簡報</div><h4>目前無法生成</h4><p>n8n workflow 已進入 dead letter。請先檢查 workflow 連到的後端網址是否仍然有效。</p></div>';
          setStatus(`n8n 執行失敗：${data.stage || "unknown stage"}`, true);
          return;
        }

        document.getElementById("caseId").textContent = data.case_id || "-";
        document.getElementById("caseStatus").textContent = data.status || "-";
        document.getElementById("reviewStatus").textContent = data.review_status || "-";
        document.getElementById("riskScore").textContent = data.module_a_report?.risk_score ?? "-";
        document.getElementById("complianceRisk").textContent = data.compliance_report?.risk_level || "-";
        document.getElementById("slideCount").textContent = data.presentation?.page_count ?? "-";
        const googleSlidesUrl = data.presentation?.google_presentation_url || data.raw_case?.presentation_result?.google_presentation_url || null;
        document.getElementById("googleSlidesStatus").textContent = googleSlidesUrl ? "已建立" : "未啟用";
        document.getElementById("technicalSummary").textContent = data.module_a_report?.technical_summary || "尚未產生輸出。";
        document.getElementById("advisorNarrative").textContent = data.module_a_report?.advisor_narrative || "尚未產生輸出。";
        document.getElementById("approvalRecommendation").textContent = data.compliance_report?.approval_recommendation || "尚未產生輸出。";
        setList("recommendations", data.module_a_report?.recommendations || []);
        setList("disclosures", data.compliance_report?.required_disclosures || []);
        setList("issues", data.compliance_report?.issues || [], (item) => `${item.severity}: ${item.claim} - ${item.reason}`);

        const slidesEl = document.getElementById("slides");
        slidesEl.innerHTML = "";
        (data.presentation?.pages || []).forEach((slide, index) => {
          const node = document.createElement("article");
          node.className = "slide";
          node.innerHTML = `
            <div class="slide-head">第 ${index + 1} 頁</div>
            <div class="slide-section">${slide.section || "簡報頁"}</div>
            <h4>${slide.title || `第 ${index + 1} 頁`}</h4>
            <div class="slide-key">${slide.key_message || ""}</div>
            <div class="slide-grid">
              <div class="slide-block">
                <h5>分析重點</h5>
                <p>${slide.supporting_points || slide.content || ""}</p>
              </div>
              <div class="slide-block">
                <h5>顧問建議</h5>
                <p>${slide.advisor_recommendation || ""}</p>
              </div>
            </div>
            <div class="slide-footer">
              <div class="footer-meta">ET Consulting | Insurance Advisory Proposal</div>
              <div class="slide-visual">
                <div class="visual-icon">${slide.visual_icon || "📘"}</div>
              </div>
            </div>`;
          slidesEl.appendChild(node);
        });
        if (!(data.presentation?.pages || []).length) {
          slidesEl.innerHTML = '<div class="slide"><div class="slide-head">簡報</div><h4>沒有輸出</h4><p>目前沒有返回任何簡報頁面。</p></div>';
        }

        if (data.case_id) {
          const report = document.createElement("a");
          report.href = `/api/cases/${data.case_id}?key=ins-agent-2026-key`;
          report.target = "_blank";
          report.textContent = "開啟案件報告 JSON";
          linkBar.appendChild(report);

          const presentation = document.createElement("a");
          presentation.href = `/api/cases/${data.case_id}/presentation?key=ins-agent-2026-key`;
          presentation.target = "_blank";
          presentation.textContent = "開啟簡報檢視";
          linkBar.appendChild(presentation);

          if (googleSlidesUrl) {
            const googleSlides = document.createElement("a");
            googleSlides.href = googleSlidesUrl;
            googleSlides.target = "_blank";
            googleSlides.textContent = "開啟 Google Slides";
            linkBar.appendChild(googleSlides);
          }
        }

        const modeText = data.execution_mode === "local_fallback" ? "本地 fallback" : "n8n";
        setStatus(`已完成。案件 ${data.case_id} 已生成。執行模式：${modeText}`);
        await loadGoogleStatus();
      } catch (error) {
        setStatus(error.message || "生成失敗", true);
      }
    });

    loadGoogleStatus();
  </script>
</body>
</html>
"""


@router.post("/workbench/generate")
def workbench_generate(req: IntakeRequest) -> JSONResponse:
    try:
        status_code, body = _post_to_n8n_webhook(req.model_dump(mode="json"))
        if status_code < 400 and not body.get("dead_letter"):
            return JSONResponse(status_code=status_code, content=body)
    except URLError:
        pass
    except Exception:
        pass

    status_code, body = _run_local_workbench(req)
    return JSONResponse(status_code=status_code, content=body)
