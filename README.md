# Tender Analyser

Self-hosted API + simple web UI: upload tender **PDF, DOCX, XLSX, or ZIP**, get structured **PQ/TQ-style tables** (RFP vs company marks where the document allows), **✔ / ✘ / ⚠** row symbols, **pre-bid queries** with **drafted/sent/closed** status in SQLite, **compliance checklist** with optional **document vault** hints, **governance + risk** blocks, a **rule-based confidence score** (transparent heuristic — not ML), **audit log** per action, and **filesystem exports** under `data/reports/{tender_id}/` (JSON, markdown outline, summary PDF). **Raw text is stored** so **re-analyse** works without re-uploading.

**Not in this repo (integrate later):** knowledge graph, nightly learning, ERP/Slack, Google Sheets profile sync, RBAC, encryption — hooks are documented below.

## Quick start (local)

1. **Python 3.11+** recommended.

2. Create a virtual environment and install dependencies:

   ```bash
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   ```

3. Copy `.env.example` to `.env` and set your API key from [Google AI Studio](https://aistudio.google.com/apikey):

   ```env
   GEMINI_API_KEY=your_key_here
   ```

4. Edit **`company_profile.md`** with your real company facts (certs, turnover, projects). The model uses this for PQ/TQ-style assessment.

5. Run the server from the **project root** (`tender-analyzer/`):

   ```bash
   uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
   ```

6. Open **http://localhost:8000** for the upload UI, or **http://localhost:8000/docs** for Swagger.

## GitHub

If `git` is not installed locally, install [Git for Windows](https://git-scm.com/download/win), then:

```bash
cd tender-analyzer
git init
git add .
git commit -m "Initial tender analyser"
```

Create a new empty repository on GitHub, add the remote, and push:

```bash
git remote add origin https://github.com/YOUR_USER/tender-analyzer.git
git branch -M main
git push -u origin main
```

Enable **GitHub Actions** (default `.github/workflows/ci.yml` runs tests on push/PR).

## Docker (fully automated run)

```bash
cp .env.example .env
# edit .env — add GEMINI_API_KEY
docker compose up --build
```

API on **http://localhost:8000**. Data volume persists `data/tenders.db` and `data/reports/`.

### Optional: scanned PDFs (OCR)

Install [Tesseract](https://github.com/tesseract-ocr/tesseract) for your OS, then:

```bash
pip install -r requirements-ocr.txt
```

Thin digital PDF extracts automatically retry OCR when `ENABLE_PDF_OCR` is true (default).

### Optional: document vault

Create a folder (e.g. `document_vault/`) with COI, GST, PAN, ISO PDFs. Set `DOCUMENT_VAULT_PATH=document_vault` in `.env`. The **dashboard** endpoint suggests filename matches for checklist rows (expiry tracking is still manual).

## Enterprise-style workflow (what is implemented)

| Spec area | Implementation |
|-----------|----------------|
| Upload & parsing | PDF, DOCX, XLSX, TXT, HTML, ZIP; optional OCR retry |
| PQ/TQ tables | RFP marks + company estimate fields; `audit_status` Met/Critical/Pending; row `status_symbol` ✔✘⚠ |
| Pre-bid queries | Stored in `prebid_queries` table; `PATCH .../prebid` sets `drafted\|sent\|closed\|withdrawn` |
| Compliance checklist | AI-generated + `vault_tag`; vault filename hints on dashboard |
| Submission pack | `data/reports/{id}/submission_pack_outline.md` + `submission_pack.pdf` (summary) |
| Dashboard | `GET /api/tenders/{id}/dashboard` — overview, PQ/TQ, pre-bid, risks, governance, exports |
| Audit trail | `audit_log` table; optional header `X-User-Id`; `GET /api/tenders/{id}/audit` |
| Confidence | `confidence_score` + `confidence_basis` (PQ/TQ colour counts + verdict rules) |
| Knowledge graph / learning | **Out of scope** — replace `compute_confidence_score` later |
| Google Sheets profile sync | **Out of scope** — keep `company_profile.md` as source of truth in git |

## API summary

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/health` | Liveness, AI key, vault, OCR flags |
| `POST` | `/api/analyse` | Multipart `files` + optional `tender_id`; header `X-User-Id` optional |
| `GET` | `/api/tenders` | List recent tenders |
| `GET` | `/api/tenders/{id}` | Full analysis (pre-bid status synced from DB) |
| `GET` | `/api/tenders/{id}/dashboard` | UI-friendly aggregate + vault hints |
| `PATCH` | `/api/tenders/{id}/prebid` | JSON `{"q_index":0,"status":"sent"}` |
| `GET` | `/api/tenders/{id}/audit` | Audit entries for this tender |
| `GET` | `/api/audit` | Recent global audit entries |
| `POST` | `/api/tenders/{id}/reanalyse` | Re-run AI on stored text |
| `DELETE` | `/api/tenders/{id}` | Remove tender + pre-bid rows (audit retained) |

## Deploy (examples)

- **Railway / Render / Fly.io**: set `GEMINI_API_KEY`, start command `uvicorn app.main:app --host 0.0.0.0 --port $PORT` (or map their `PORT` env).
- **Docker**: use the included `Dockerfile`; mount `/app/data` if you need a persistent database on the host.

## Security

- Never commit `.env` or real API keys.
- Put the service behind HTTPS and authentication if exposed to the internet.

## Licence

MIT — use and modify freely.
