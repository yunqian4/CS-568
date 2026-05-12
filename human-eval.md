# Human Evaluation Workflow

Use `human-study/` as the study designer workspace. A designer should not need to edit backend or frontend internals for normal study setup.

## 1. Add A PDF

Create one subdirectory per reusable document:

```powershell
mkdir human-study\pdfs\my-paper
Copy-Item path\to\paper.pdf human-study\pdfs\my-paper\source.pdf
```

Optional per-document config overrides can go here:

```text
human-study/pdfs/my-paper/config.json
```

If no override exists, the pipeline uses:

```text
human-study/config/default_document_config.json
```

## 2. Extract Text Chunks And Paragraphs

Run:

```powershell
backend\.venv\Scripts\python.exe human-study\scripts\1_extract_document.py --document my-paper
```

Outputs:

```text
human-study/documents/my-paper/source.pdf
human-study/documents/my-paper/config.json
human-study/documents/my-paper/chunks.json
human-study/documents/my-paper/paragraphs.json
human-study/documents/my-paper/document.json
```

You can stop here and edit `chunks.json`, `paragraphs.json`, or `document.json`. The next stage reads these editable files.

## 3. Generate Representations

Edit prompts, thresholds, colors, model, or OpenAI settings in:

```text
human-study/documents/my-paper/config.json
```

Then run:

```powershell
backend\.venv\Scripts\python.exe human-study\scripts\2_generate_representations.py --document my-paper
```

Outputs:

```text
human-study/documents/my-paper/providers/opendataloader/llm/representations/
human-study/documents/my-paper/document.json
human-study/documents/my-paper/paragraphs.json
```

You can edit generated representations in `document.json` or `paragraphs.json` before export.

## 4. Create Exam Settings

Create or edit files in:

```text
human-study/exams/
```

Each exam JSON chooses a reusable document and defines:

- exam id and title
- `document_id`
- `question_set_id`
- `time_limit_seconds`
- `representation_condition.visible_representations`
- exam questions with private `answer_index`
- pre/post questionnaires

The repo includes examples:

```text
human-study/exams/wikiworkshop-keywords-example.json
human-study/exams/wikiworkshop-summary-example.json
```

The same document can be reused in multiple exam files with different questions, time limits, or representation visibility.

## 5. Configure The Global Scheduler

Edit:

```text
human-study/scheduler.json
```

The default scheduler is weighted random:

```json
{
  "strategy": "weighted_random",
  "exam_settings": [
    { "exam_id": "wikiworkshop-keywords-example", "weight": 1 },
    { "exam_id": "wikiworkshop-summary-example", "weight": 1 }
  ]
}
```

Cloudflare participants are assigned from this scheduler when they open `/study`.

## 6. Export For Cloudflare

Run:

```powershell
backend\.venv\Scripts\python.exe human-study\scripts\3_export_cloudflare.py
```

Public output:

```text
frontend/public/study-cache/
```

Private R2 answer-key output:

```text
human-study/private-r2/study-private/answer-keys/
```

Public question files do not contain `answer_index`. Confirm:

```powershell
rg "answer_index" frontend\public\study-cache\questions
```

No output means the public files are safe.

## 7. Preview

Run:

```powershell
run-test.bat
```

Open:

```text
http://127.0.0.1:8788/study
```

Local preview can load the study and static documents. Real submission scoring needs the `STUDY_RESULTS` R2 binding and uploaded private answer keys.

## 8. Upload Private Answer Keys

Upload each file in `human-study/private-r2/study-private/answer-keys/`:

```powershell
npx wrangler r2 object put pdfreader-study-results/study-private/answer-keys/wikiworkshop-2025-keywords.json --file human-study/private-r2/study-private/answer-keys/wikiworkshop-2025-keywords.json
```

Repeat for every exported answer-key JSON.

## 9. Deploy

Cloudflare Pages settings:

- build command: `cd frontend && npm.cmd run build`
- build output directory: `frontend/dist`
- R2 binding: `STUDY_RESULTS`

Participants use:

```text
https://<your-pages-domain>/study
```

Submissions are stored as immutable JSON objects:

```text
study-results/<study_id>/<yyyy-mm-dd>/<session_id>.json
```
