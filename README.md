# PDFReader

Prototype web-based PDF reader modeled after the sibling `ZoomableReader` project.

## Features

- Upload a local PDF or import one from a URL.
- Parse PDFs through selectable backend providers.
- Use OpenDataLoader PDF extraction for semantic chunks, reading order, and bounding boxes.
- Use LLM semantic grouping and progressive LLM keyword/summary generation.
- Render the PDF in the browser with chunk frames and representation overlays.

## Prerequisites

Install these tools before running the OpenDataLoader + LLM pipeline:

- Git
- Python 3.14+
- Node.js LTS with npm
- Java 11+
- An OpenAI API key

Check versions:

```powershell
git --version
py -3.14 --version
node --version
npm --version
java -version
```

## Clone

```powershell
git clone https://github.com/yunqian4/CS-568.git
cd CS-568
```

## Backend Setup

From the repository root:

```powershell
cd backend
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[opendataloader]"
```

Create the backend environment file:

```powershell
Copy-Item .env.example .env
```

Edit `backend\.env`:

```env
OPENAI_API_KEY=your_openai_api_key_here
OPENAI_REPRESENTATION_MODEL=gpt-5-nano
```

The OpenAI key can also be entered in the browser UI per request. Do not commit `.env`.

## Run Backend

Run the backend from the repository root:

```powershell
cd ..
backend\.venv\Scripts\python.exe -m uvicorn backend.app:app --reload
```

The backend listens at:

```text
http://127.0.0.1:8000
```

Health check:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/health
```

Expected result:

```json
{"status":"ok"}
```

## Frontend Setup

Open a second PowerShell terminal:

```powershell
cd CS-568\frontend
npm install
npm run dev
```

The frontend listens at:

```text
http://127.0.0.1:5173
```

The Vite dev server proxies `/api` requests to the backend.

## Use OpenDataLoader + LLM

In the browser:

1. Open `http://127.0.0.1:5173`.
2. Under **Segmentation**, choose `OpenDataLoader PDF`.
3. Keep **Use LLM** checked.
4. If `backend\.env` has `OPENAI_API_KEY`, leave the OpenAI key field blank.
5. Otherwise, enter an OpenAI key in the UI.
6. Leave the model field blank to use the server default, or enter `gpt-5-nano`.
7. Upload a PDF or enter a PDF URL.
8. Click **Confirm**.

The backend will store the PDF under `dat/temp/<sha256>/`, run OpenDataLoader PDF extraction, apply LLM semantic grouping, generate progressive keyword and summary representations, and let the frontend poll representation status.

## Verify

Backend tests:

```powershell
cd CS-568\backend
.\.venv\Scripts\python.exe -m unittest tests.test_pdf_ingest
```

Frontend build:

```powershell
cd CS-568\frontend
npm run build
```

## Human Study Deployment

The `/study` route is designed for Cloudflare Pages deployments without the FastAPI backend. All study preparation lives under `human-study/`: PDFs, editable intermediate files, exam settings, scheduler, export scripts, and private R2 answer keys.

Add a PDF:

```powershell
mkdir human-study\pdfs\my-paper
Copy-Item path\to\paper.pdf human-study\pdfs\my-paper\source.pdf
```

Extract editable chunks and paragraphs:

```powershell
backend\.venv\Scripts\python.exe human-study\scripts\1_extract_document.py --document my-paper
```

Generate editable representations:

```powershell
backend\.venv\Scripts\python.exe human-study\scripts\2_generate_representations.py --document my-paper
```

Edit exam settings in `human-study\exams\` and participant assignment in `human-study\scheduler.json`, then export the static study cache:

```powershell
backend\.venv\Scripts\python.exe human-study\scripts\3_export_cloudflare.py
```

This writes public assets under `frontend\public\study-cache\` and private R2 answer keys under `human-study\private-r2\`.

See `human-eval.md` for the complete test creation and deployment workflow.

Build the frontend:

```powershell
cd frontend
npm.cmd run build
```

Preview the Pages deployment from the repo root:

```powershell
npx wrangler pages dev frontend/dist
```

Or run the cached-study export, frontend build, and local Pages preview together:

```powershell
run-test.bat
```

Configure an R2 binding named `STUDY_RESULTS`. Upload private answer keys before running a study:

```powershell
npx wrangler r2 object put pdfreader-study-results/study-private/answer-keys/wikiworkshop-2025-keywords.json --file human-study/private-r2/study-private/answer-keys/wikiworkshop-2025-keywords.json
```

Participant submissions are written to:

```text
study-results/<study_id>/<yyyy-mm-dd>/<session_id>.json
```

## Common Issues

If OpenDataLoader fails, check Java:

```powershell
java -version
```

If the app says an API key is required, add `OPENAI_API_KEY` to `backend\.env` or paste the key into the UI.

If backend imports fail, make sure it was launched from the repository root:

```powershell
backend\.venv\Scripts\python.exe -m uvicorn backend.app:app --reload
```
