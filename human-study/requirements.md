# Human Study Requirements

This file summarizes the human-study requirements discussed in the thread.

## Deployment Model

- Deploy the participant-facing interface on Cloudflare Pages.
- Do not require the Python FastAPI backend, OpenDataLoader, Java, or live OpenAI calls in the deployed participant flow.
- Use Cloudflare Pages static assets for cached study documents and public study settings.
- Use Cloudflare Pages Functions for server-side scoring and result submission.
- Use Cloudflare R2 as file-like storage for private answer keys and participant result JSON files.
- Store one immutable result JSON file per participant session instead of appending to a shared file.

## Participant Flow

- Keep the existing upload/debug reader at `/`.
- Add a controlled participant route at `/study`.
- Generate or reuse an anonymous browser participant id.
- Assign each participant through a global scheduler.
- Run the study as:
  1. pre-questionnaire
  2. cached PDF reading task
  3. exam questions
  4. post-questionnaire
  5. result submission
- Record assignment, timing, questionnaire responses, exam answers, and server-side score.
- Do not expose answer keys in public frontend assets.

## Designer Workflow

- A study designer should only need to work inside the root `human-study/` directory.
- To submit a PDF, the designer should move it to:

```text
human-study/pdfs/<document-id>/source.pdf
```

- The designer should be able to customize defaults by editing JSON files, not backend/frontend source code.
- The pipeline should be interruptible: after each stage, the designer can inspect and edit intermediate JSON before continuing.

## Document Preparation Pipeline

- Stage 1 extracts text chunks and paragraph segmentation/concatenation:

```powershell
backend\.venv\Scripts\python.exe human-study\scripts\1_extract_document.py --document <document-id>
```

- Stage 1 outputs editable files:

```text
human-study/documents/<document-id>/chunks.json
human-study/documents/<document-id>/paragraphs.json
human-study/documents/<document-id>/document.json
human-study/documents/<document-id>/config.json
```

- Stage 2 generates editable representations from the editable document JSON:

```powershell
backend\.venv\Scripts\python.exe human-study\scripts\2_generate_representations.py --document <document-id>
```

- Stage 2 outputs representation files and merges generated representations back into editable document JSON.
- Extracted text chunks, paragraph segmentation, and generated representations should all remain editable before export.

## Exam Settings

- Exam settings should be JSON files under:

```text
human-study/exams/
```

- Each exam setting should define:
  - exam id and title
  - reusable `document_id`
  - `question_set_id`
  - exam questions and private answer indexes
  - time limit
  - representation visibility condition
  - pre-questionnaire fields
  - post-questionnaire fields
- A single PDF and its generated representations may be reused across multiple exam settings.

## Scheduler

- Global participant assignment should be configured in:

```text
human-study/scheduler.json
```

- The first scheduler strategy is weighted random assignment across enabled exam settings.
- The exported `/study` route should assign participants using this scheduler.

## Export And Preview

- Export Cloudflare-ready assets with:

```powershell
backend\.venv\Scripts\python.exe human-study\scripts\3_export_cloudflare.py
```

- Public export target:

```text
frontend/public/study-cache/
```

- Private R2 answer-key export target:

```text
human-study/private-r2/study-private/answer-keys/
```

- `run-test.bat` should export, build, and start a local Cloudflare Pages preview for `/study`.

## Example Requirement

- Provide a complete example in `human-study/` so designers can copy the pattern.
- The example should include:
  - an example PDF
  - editable chunk and paragraph intermediate files
  - generated representation files
  - at least one exam setting
  - scheduler entries
  - exported public assets without answer keys
