# PwC Hybrid Automation (Node.js + Python)

This repository contains two cooperating services:
- Node.js: Playwright headless login with Gmail OTP, auto-discovers PwC API endpoints, schedules runs every 105 minutes.
- Python (FastAPI): Uses discovered APIs to export dashboard data, fetch candidate profiles and documents, convert PIF PDF → JSON, upload to Google Drive, and sync tabular data to Google Sheets with incremental + audit logging.

## Environment Variables
Set these in Replit/Render secrets (exact names):

### Required
- `PWC_USERNAME` - PwC login email
- `PWC_PASSWORD` - PwC login password
- `GMAIL_CLIENT_ID` - Google OAuth client ID for Gmail API
- `GMAIL_CLIENT_SECRET` - Google OAuth client secret
- `GMAIL_REFRESH_TOKEN` - Gmail API refresh token
- `GOOGLE_CREDENTIALS_JSON` - Service account JSON as single line (for Drive + Sheets)
- `GOOGLE_DRIVE_FOLDER_ID` - Google Drive folder ID for candidate uploads
- `GOOGLE_SHEET_ID` - Google Sheets spreadsheet ID

### Optional
- `GMAIL_POLL_LABEL` - Gmail label/folder for OTP emails (default: INBOX)
- `RUN_INTERVAL_MINUTES` - Scheduler interval in minutes (default: 105)
- `EXPORT_SERVICE_URL` - Python service URL (default: http://localhost:8000)
- `RUN_AS_ROOT` - Run Playwright as root (default: false)
- `PLAYWRIGHT_BROWSERS_PATH` - Custom Playwright browser path
- `NODE_ENV` - Node environment (default: production)
- `LOG_LEVEL` - Logging level (default: info)

### Scalability & Rate Limiting (Python Service)
These control how fast candidates and documents are processed. Adjust based on server capacity and API rate limits:

- `CANDIDATE_PROCESS_DELAY` - Delay between processing candidates (seconds, default: 0.5)
  - Lower = faster but more server load
  - Higher = slower but safer for rate limits
- `DOCUMENT_DOWNLOAD_DELAY` - Delay between document downloads (seconds, default: 0.3)
  - Lower = faster document downloads
  - Higher = more conservative rate limiting
- `MAX_CONCURRENT_CANDIDATES` - Maximum candidates processed simultaneously (default: 5)
  - Higher = faster processing but more memory/CPU usage
  - Lower = slower but more stable
  - Recommended: 3-10 depending on server capacity
- `MAX_CONCURRENT_DOCUMENTS` - Maximum documents downloaded per candidate simultaneously (default: 3)
  - Note: Currently not used, documents are downloaded sequentially per candidate

## Run (Replit)
- Replit Always On (paid) recommended.
- `.replit` runs `start.sh`, which installs dependencies and runs both services.
- `replit.nix` provisions system libraries including tesseract and poppler.

## Run (Local)
```bash
python -m venv .venv && source .venv/bin/activate
python -m pip install -r python/requirements.txt
npm install
npx playwright install chromium
UVICORN_PORT=8000 uvicorn python.main:app --host 0.0.0.0 --port 8000 &
node node/scheduler.js
```

## Manual Triggers
- Node: `POST http://localhost:3000/login-and-run`
- Python: `POST http://localhost:8000/trigger-fetch` with body `{ session_id, storage_state, api_map }`
- Python (Sheets only): `POST http://localhost:8000/upload-to-sheets`
- Health: `GET /health` on both services

## Gmail OTP
Uses Gmail API via OAuth2 refresh token to fetch the latest OTP email and extract 6-digit codes.

## Google Drive & Sheets
- Service account JSON via `GOOGLE_CREDENTIALS_JSON`.
- Drive upload creates folder `<GOOGLE_DRIVE_FOLDER_ID>/<CandidateID> - <Name>`.
- Sheets sync uses incremental + audit (Audit Log sheet) semantics.

## Scheduler
- Runs every 105 minutes (`RUN_INTERVAL_MINUTES`).
- On each run: login + API discovery, POST to Python `/trigger-fetch` including storage_state and discovered api_map.

## Candidate Processing
- **Processes ALL candidates** from exported Excel files (no limit)
- **Downloads ALL documents** for each candidate (no limit)
- Uses **concurrent processing** with bounded parallelism for scalability
- **API-first approach**: Tries API endpoints, falls back to Playwright browser automation if API fails
- Progress logging shows `[index/total]` for each candidate
- Configurable rate limits via environment variables for scalability

## PDF → JSON
- Attempts text extraction with pdfplumber / PyPDF2; fallback OCR via Tesseract.
- Saves `pif.json` alongside `pif.pdf`.

## Deploy on Render
- Create two services (recommended):
  - Web: Python `uvicorn python.main:app --host 0.0.0.0 --port $PORT`
  - Worker: Node `node node/scheduler.js`
- Set environment variables on both.

## Examples
- `examples/storage_state.json` and `examples/pwc_api_map.json` can be added as skeletons for testing (not included by default).

## Debugging
- Check Node logs for `[API Discovery]` messages
- Check Python logs for `tabs` and `candidates` summaries
- Screenshots and artifacts saved under `/tmp`


