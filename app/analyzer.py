"""
Two-call Gemini pipeline: (1) structured tender extraction (2) bid decision + queries.
Designed for Indian government / e-procurement style documents.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from app.postprocess import enrich_analysis

logger = logging.getLogger(__name__)

GEMINI_MODELS = [
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-1.5-flash",
]

EXTRACTION_PROMPT = """You extract structured data from an Indian government / e-procurement tender document.

Company profile (for context only — do not invent facts about the company):
---
{company_profile}
---

Tender text (may be truncated):
---
{text}
---

Return ONLY valid JSON (no markdown fences). Use "—" for unknown string fields. Use [] for empty arrays.

{{
  "tender_no": "",
  "tender_name": "",
  "org_name": "",
  "dept_name": "",
  "portal": "",
  "mode_of_selection": "",
  "bid_submission_date": "",
  "bid_opening_date": "",
  "prebid_meeting": "",
  "prebid_query_date": "",
  "estimated_cost": "",
  "tender_fee": "",
  "emd": "",
  "emd_exemption": "",
  "performance_security": "",
  "contract_period": "",
  "bid_validity": "",
  "jv_allowed": "",
  "contact": "",
  "location": "",
  "scope_background": "",
  "scope_items": [{{"title": "", "description": "", "deliverables": []}}],
  "pq_criteria": [
    {{
      "sl_no": "1",
      "clause_ref": "",
      "criteria": "What the RFP asks (verbatim where possible)",
      "details": "Documents / proof required",
      "rfp_marks_allocated": "Marks or weight if stated in RFP, else —",
      "company_marks_or_estimate": "Our score or estimate vs requirement, else —",
      "company_status": "Met | Not Met | Conditional",
      "audit_status": "Met | Critical | Pending",
      "status_color": "GREEN | RED | AMBER",
      "remark": "Evidence from company profile; gaps for OEM/solvency/local office if relevant"
    }}
  ],
  "tq_criteria": [
    {{
      "sl_no": "1",
      "criteria": "",
      "details": "Max marks in RFP if stated",
      "rfp_marks_allocated": "",
      "company_marks_or_estimate": "Estimated score range vs max marks",
      "company_status": "Met | Conditional | Not Met",
      "audit_status": "Met | Critical | Pending",
      "status_color": "GREEN | AMBER | RED",
      "remark": ""
    }}
  ],
  "payment_terms": [{{"milestone": "", "payment_percent": "", "timeline": ""}}],
  "penalty_clauses": [{{"type": "", "condition": "", "penalty": ""}}]
}}

Rules:
- pq_criteria: copy eligibility/PQ text faithfully; assess against the company profile only where evidence exists.
- audit_status: Critical = hard disqualifier or missing proof; Pending = needs clarification/OEM letter/solvency cert/local office proof; Met = clearly satisfied.
- Explicitly flag in remark when risks involve OEM authorization, solvency/banking, statutory local office/registration, or unrealistic timelines.
- If no PQ table exists, return "pq_criteria": [].
- scope_items: list major work packages from the document.
"""


VERDICT_PROMPT = """You are a bid manager. Using the company profile and extracted tender JSON, produce a Bid / No-Bid decision.

Company profile:
---
{company_profile}
---

Extracted tender JSON:
---
{extraction_json}
---

Return ONLY valid JSON (no markdown):
{{
  "verdict": "BID | NO-BID | CONDITIONAL",
  "verdict_color": "GREEN | RED | AMBER",
  "reason": "2-4 sentences with concrete references to criteria or scope",
  "key_reasons": ["", ""],
  "prebid_queries": [
    {{
      "clause": "",
      "rfp_text": "",
      "query": "",
      "desired_clarification": ""
    }}
  ],
  "action_items": [
    {{"action": "", "responsible": "", "target_date": "", "priority": "URGENT | HIGH | MEDIUM"}}
  ],
  "notes": [{{"title": "", "detail": ""}}],
  "submission_checklist": [{{"document": "", "annexure": "", "status": "Prepare | Ready | CRITICAL", "vault_tag": "COI|GST|PAN|ISO|CMMI|OEM|Turnover|Solvency|Other"}}],
  "risk_highlights": [
    {{"category": "OEM authorization | Solvency | Local office | Timeline | Penalty/LD | Conflict of interest | Other", "severity": "high | medium | low", "detail": ""}}
  ],
  "governance_report": {{
    "audit_notes": "What was analysed, assumptions, and what humans must verify",
    "retention_recommendation_years": 7,
    "compliance_frameworks": ["GeM", "nProcure", "CVC guidelines (high level)"],
    "ethics_and_ci_flags": ["Flag unrealistic timelines, hidden penalties, or conflict-of-interest cues if any"]
  }},
  "overall_verdict": {{
    "verdict": "BID | NO-BID | CONDITIONAL",
    "color": "GREEN | RED | AMBER",
    "green": 0,
    "amber": 0,
    "red": 0,
    "reason": ""
  }}
}}

Set overall_verdict.green/amber/red counts from pq_criteria + tq_criteria status_color in the extraction (count GREEN/AMBER/RED).
Align overall_verdict.verdict and verdict with the same decision.
Maximum 5 prebid_queries; only genuine gaps affecting eligibility or scoring — prioritise ⚠/✘ style gaps (OEM, solvency, local presence).
"""


def clean_json(text: str) -> dict[str, Any]:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s*```$", "", text, flags=re.MULTILINE)
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    raise json.JSONDecodeError("Cannot parse model JSON", text, 0)


def _call_gemini(prompt: str, api_key: str, timeout: float = 120.0) -> str:
    last_err: str | None = None
    with httpx.Client(timeout=timeout) as client:
        for model in GEMINI_MODELS:
            url = (
                "https://generativelanguage.googleapis.com/v1beta/models/"
                f"{model}:generateContent?key={api_key}"
            )
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.15, "maxOutputTokens": 8192},
            }
            try:
                r = client.post(url, json=payload)
                if r.status_code in (429, 500, 503):
                    last_err = f"{model} HTTP {r.status_code}"
                    continue
                r.raise_for_status()
                data = r.json()
                parts = data["candidates"][0]["content"]["parts"]
                return parts[0]["text"]
            except Exception as e:
                last_err = str(e)
                logger.warning("Gemini %s failed: %s", model, e)
                continue
    raise RuntimeError(last_err or "All Gemini models failed")


def analyze_tender(full_text: str, company_profile: str, api_keys: list[str]) -> dict[str, Any]:
    """
    Run extraction + verdict. Returns a flat dict suitable for API/DB storage.
    """
    if not api_keys:
        return {
            "error": "No GEMINI_API_KEY configured. Copy .env.example to .env and add your key.",
        }

    text = full_text.strip()
    if len(text) > 280_000:
        text = text[:280_000] + "\n\n[TRUNCATED]"

    if len(text) < 200:
        return {"error": "Document text too short after extraction. Check PDF/DOCX quality."}

    extraction: dict[str, Any] = {}
    last_key_error: str | None = None

    for key in api_keys:
        try:
            p1 = EXTRACTION_PROMPT.format(company_profile=company_profile, text=text[:200_000])
            raw1 = _call_gemini(p1, key)
            extraction = clean_json(raw1)
            break
        except Exception as e:
            last_key_error = str(e)
            err_l = str(e).lower()
            if "429" in err_l or "quota" in err_l or "resource" in err_l:
                continue
            if "401" in err_l or "403" in err_l or "api key" in err_l:
                return {"error": f"Gemini API key rejected: {e}"}
            continue

    if not extraction:
        return {
            "error": f"Extraction failed after trying keys: {last_key_error}",
            "ai_warning": last_key_error,
        }

    verdict_blob: dict[str, Any] = {}
    for key in api_keys:
        try:
            ej = json.dumps(extraction, ensure_ascii=False)[:100_000]
            p2 = VERDICT_PROMPT.format(company_profile=company_profile, extraction_json=ej)
            raw2 = _call_gemini(p2, key)
            verdict_blob = clean_json(raw2)
            break
        except Exception as e:
            last_key_error = str(e)
            continue

    if not verdict_blob:
        verdict_blob = {
            "verdict": "CONDITIONAL",
            "verdict_color": "AMBER",
            "reason": "Verdict step failed; manual review required.",
            "key_reasons": [last_key_error or "unknown"],
            "prebid_queries": [],
            "action_items": [],
            "notes": [],
            "submission_checklist": [],
            "overall_verdict": {
                "verdict": "CONDITIONAL",
                "color": "AMBER",
                "green": 0,
                "amber": 0,
                "red": 0,
                "reason": last_key_error or "",
            },
        }

    merged: dict[str, Any] = {**extraction, **verdict_blob}
    v = merged.get("verdict") or merged.get("overall_verdict", {}).get("verdict") or "CONDITIONAL"
    merged["verdict"] = str(v).upper().replace(" ", "-") if isinstance(v, str) else "CONDITIONAL"
    ov = merged.get("overall_verdict")
    if isinstance(ov, dict) and "verdict" not in ov:
        ov["verdict"] = merged["verdict"]
    merged.setdefault("governance_report", {})
    merged.setdefault("risk_highlights", [])
    return enrich_analysis(merged)
