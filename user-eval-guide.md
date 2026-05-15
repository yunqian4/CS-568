# User Evaluation Guide

This guide explains the full workflow for preparing a document, creating exams, deploying to Cloudflare Pages, and retrieving submitted answers.

## 1. Local Setup

Run the local backend and frontend from the repo root:

```powershell
backend\.venv\Scripts\python.exe -m uvicorn backend.app:app --reload
```

In another terminal:

```powershell
cd frontend
npm.cmd run dev
```

Open the local designer routes:

```text
http://127.0.0.1:5173/prepare-document
http://127.0.0.1:5173/exam-designer
```

## 2. Prepare A Document

Use `/prepare-document` for the visual workflow.

1. Open `http://127.0.0.1:5173/prepare-document`.
2. In `Document upload and preprocess`, choose an existing current document or upload a new PDF.
3. Optional: enter a stable document id such as `my-paper`.
4. Click `Process document`.
5. The backend runs OpenDataLoader only. It does not run LLM paragraph matching and does not generate representations during upload.
6. In `Paragraph matching`, click chunk rows to toggle whether each chunk belongs to the current paragraph.
7. Use `Add above` and `Add below` to insert paragraph rows.
8. Check the text preview for each paragraph.
9. Click `Save document`.
10. In `Representation generation`, edit prompts and visible settings.
11. Click `Apply` to generate representations. Apply uses up to 4 concurrent OpenAI calls and shows progress.
12. Click `Save representations`.

Saved files are under:

```text
human-study/documents/<document-id>/
```

Important files:

```text
source.pdf
document.json
chunks.json
paragraphs.json
config.json
representation-prompts.json
representations.json
providers/opendataloader/llm/representations/
```

You can also prepare documents with scripts:

```powershell
backend\.venv\Scripts\python.exe human-study\scripts\1_extract_document.py --document <document-id>
backend\.venv\Scripts\python.exe human-study\scripts\2_generate_representations.py --document <document-id>
```

For script mode, place the PDF at:

```text
human-study/pdfs/<document-id>/source.pdf
```

## 3. Create Exams

Use `/exam-designer` for exam setup.

1. Open `http://127.0.0.1:5173/exam-designer`.
2. Click `New`, or select an existing exam.
3. Set `Exam id` and `Title`.
4. Choose exactly one prepared document.
5. Choose timer mode:
   - `Countdown`: the exam auto-submits when time expires.
   - `Record reading time from 0`: the timer counts up and records elapsed time.
6. For countdown exams, set `Time limit seconds`.
7. Select visible representations with the checkboxes. Multiple selections are allowed.
8. Add, edit, or remove multiple-choice questions.
9. Add, edit, or remove choices for each question.
10. Select the correct answer with the radio button.
11. Click `Save exam`.

Question ids are assigned automatically from the exam id and question order. Do not manually edit question ids.

Exam JSON files are saved under:

```text
human-study/exams/
```

Each exam specifies:

```text
id
title
enabled
document_id
question_set_id
timing_mode
time_limit_seconds
representation_condition.visible_representations
questions
```

Answer keys are private in the source exam JSON through `answer_index`. The exporter removes answer keys from public question JSON.

## 4. Configure Assignment

Edit:

```text
human-study/scheduler.json
```

Example:

```json
{
  "strategy": "weighted_random",
  "exam_settings": [
    { "exam_id": "wikiworkshop-keywords-example", "weight": 1 },
    { "exam_id": "wikiworkshop-summary-example", "weight": 1 }
  ]
}
```

Participants opening `/exam` are assigned by this scheduler. If no valid scheduler weights exist, the frontend samples randomly from exported exams.

## 5. Export For Cloudflare

Run:

```powershell
backend\.venv\Scripts\python.exe human-study\scripts\3_export_cloudflare.py
```

This writes public static assets to:

```text
frontend/public/study-cache/
```

It also writes private answer keys to:

```text
human-study/private-r2/study-private/answer-keys/
```

Confirm that public question files do not contain answer keys:

```powershell
rg "answer_index" frontend\public\study-cache\questions
```

Expected result: no output.

## 6. Preview Locally

Run:

```powershell
run-test.bat
```

Open:

```text
http://127.0.0.1:8788/exam
```

Local FastAPI development also mirrors the submit endpoint:

```text
POST /api/study/score-submit
```

Local results are written under:

```text
human-study/results/<study-id>/<yyyy-mm-dd>/<session-id>.json
```

For a real Cloudflare preview or deployment, the Pages Function and R2 binding must be configured.

## 7. Deploy To Cloudflare Pages

The app can run on Cloudflare Pages without FastAPI. The deployed exam uses:

- static frontend from Cloudflare Pages
- static study cache from `frontend/public/study-cache`
- Pages Function at `/api/study/score-submit`
- R2 bucket bound as `STUDY_RESULTS`

Cloudflare Pages settings:

```text
Build command: cd frontend && npm.cmd run build
Build output directory: frontend/dist
```

R2 binding:

```text
Binding name: STUDY_RESULTS
Bucket: pdfreader-study-results
```

The repo includes `wrangler.toml` with:

```toml
name = "pdfreader-study"
compatibility_date = "2026-05-10"
pages_build_output_dir = "frontend/dist"

[[r2_buckets]]
binding = "STUDY_RESULTS"
bucket_name = "pdfreader-study-results"
preview_bucket_name = "pdfreader-study-results-preview"
```

Upload private answer keys to R2 before running a real evaluation:

```powershell
npx wrangler r2 object put pdfreader-study-results/study-private/answer-keys/<question-set-id>.json --file human-study/private-r2/study-private/answer-keys/<question-set-id>.json
```

Repeat for every file in:

```text
human-study/private-r2/study-private/answer-keys/
```

Participants use:

```text
https://<your-pages-domain>/exam
```

## 8. What Gets Submitted

The exam submits:

- participant id
- session id
- assigned exam metadata
- selected answers
- unanswered questions as `selected_index: null`
- timing metadata
- browser/client metadata
- server-side score

After a successful submit, the participant sees a completion notice and the session ends.

Submissions are stored as immutable JSON objects in R2:

```text
study-results/<study-id>/<yyyy-mm-dd>/<session-id>.json
```

## 9. Retrieve Submitted Answers

You can retrieve submissions from the Cloudflare dashboard:

1. Open Cloudflare Dashboard.
2. Go to R2.
3. Open the `pdfreader-study-results` bucket.
4. Browse `study-results/<study-id>/<date>/`.
5. Download the result JSON files.

You can also use Wrangler.

List objects:

```powershell
npx wrangler r2 object list pdfreader-study-results --prefix study-results/<study-id>/
```

Download one result:

```powershell
npx wrangler r2 object get pdfreader-study-results/study-results/<study-id>/<yyyy-mm-dd>/<session-id>.json --file result.json
```

Download answer keys if needed:

```powershell
npx wrangler r2 object get pdfreader-study-results/study-private/answer-keys/<question-set-id>.json --file answer-key.json
```

Do not put private answer keys under `frontend/public/` or any other static asset directory.

## 10. Checklist Before Launch

- Prepared document opens in `/prepare-document`.
- Paragraph mappings are saved.
- Representations are generated and saved.
- Exam exists in `/exam-designer`.
- Exam has the correct document.
- Visible representations are selected.
- Questions and answer indexes are correct.
- `human-study/scheduler.json` includes the exam.
- `3_export_cloudflare.py` runs successfully.
- Public questions contain no `answer_index`.
- Private answer keys are uploaded to R2.
- Cloudflare Pages has `STUDY_RESULTS` R2 binding.
- `/api/study/health` reports R2 configured.
- `/exam` can submit successfully on the Cloudflare URL.
