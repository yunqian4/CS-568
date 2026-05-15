# Human Evaluation Workflow

Use `human-study/` as the study designer workspace. A designer should not need to edit backend or frontend internals for normal study setup.

The simplest path is the visual designer:

```text
http://127.0.0.1:5173/prepare-document
http://127.0.0.1:5173/exam-designer
```

Select an existing current document or upload a PDF in the `Document upload and preprocess` tab, then click `Process document`. The designer processes uploads with OpenDataLoader only, opens the reader preview, and does not run LLM paragraph matching or generate representations until you edit prompts and click `Apply` in the `Representation generation` tab.

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
human-study/documents/my-paper/representation-prompts.json
```

You can stop here and edit `chunks.json`, `paragraphs.json`, or `document.json`. The next stage reads these editable files.

For visual editing, start the backend and frontend dev servers and open:

```text
http://127.0.0.1:5173/prepare-document
```

The designer route displays chunk IDs on the PDF and uses three right-pane tabs. `Paragraph matching` edits paragraph-to-chunk mappings only: click chunk rows to toggle selection for a paragraph, preview the selected text, and use `Add above` or `Add below` to insert paragraph rows. When you click `Save document`, paragraph IDs are assigned in view order as `paragraph-1`, `paragraph-2`, and so on. It saves document sidecars back into `human-study/documents/<document-id>/`. You can skip the script extraction step when you upload and process the PDF directly in `/prepare-document`.

## 3. Generate Representations

Edit prompts, thresholds, colors, model, or OpenAI settings in:

```text
human-study/documents/my-paper/config.json
human-study/documents/my-paper/representation-prompts.json
```

The `/prepare-document` route can also add, edit, remove, and save representation prompt definitions. Prompt definitions are saved to `representation-prompts.json` and mirrored into `config.json` for script compatibility, while generated representation data is saved separately to `representations.json`. In the visual workflow, representation generation is manual: edit prompts first, then click `Apply`. Apply uses up to four concurrent OpenAI representation calls and shows a progress bar while jobs run. The save buttons alert the files written under `human-study/documents/<document-id>/`.

Then run:

```powershell
backend\.venv\Scripts\python.exe human-study\scripts\2_generate_representations.py --document my-paper
```

Outputs:

```text
human-study/documents/my-paper/providers/opendataloader/llm/representations/
human-study/documents/my-paper/document.json
human-study/documents/my-paper/paragraphs.json
human-study/documents/my-paper/representations.json
```

You can edit generated representations in `document.json` or `paragraphs.json` before export.

## 4. Create Exam Settings

Create or edit files in:

```text
human-study/exams/
```

You can also use:

```text
http://127.0.0.1:5173/exam-designer
```

Each exam JSON chooses a reusable document and defines:

- exam id and title
- `document_id`
- `question_set_id`
- `timing_mode`: `countdown` or `stopwatch`
- `time_limit_seconds` for countdown exams
- `representation_condition.visible_representations`, selected through multi-select checkboxes in `/exam-designer`
- exam questions with private `answer_index`
- pre/post questionnaires

Question IDs are assigned automatically from the exam id and question order.

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

Cloudflare participants are assigned from this scheduler when they open `/study` or `/exam`.

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
http://127.0.0.1:8788/exam
```

Local preview can load the study, exam, and static documents. Real submission scoring needs the `STUDY_RESULTS` R2 binding and uploaded private answer keys.

The `/exam` route is backend-free in the Cloudflare deployment. It samples an exported exam, waits for Start, displays only the configured representation names, hides paragraph/chunk ids, records elapsed time from zero for stopwatch exams, auto-submits countdown exams when time expires, and always provides a submit button after the exam questions.
Unanswered questions are submitted as blank answers. After a successful submission, the participant is notified and the exam session ends. In local Vite/FastAPI preview, FastAPI mirrors `/api/study/score-submit`; in Cloudflare, the Pages Function handles the same endpoint.

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
