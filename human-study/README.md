# Human Study Workspace

This is the only directory a study designer needs to edit.

## Directory Map

- `pdfs/<document-id>/source.pdf`: drop each PDF here.
- `documents/<document-id>/`: editable intermediate outputs from extraction and representation generation.
- `config/default_document_config.json`: default parser and representation-generation settings.
- `exams/*.json`: exam settings, questions, time limits, representation visibility, and questionnaires.
- `scheduler.json`: global participant assignment policy for Cloudflare deployment.
- `scripts/1_extract_document.py`: extracts PDF chunks and paragraph blocks into editable JSON.
- `scripts/2_generate_representations.py`: reads editable document JSON and generates editable representations.
- `scripts/3_export_cloudflare.py`: exports public static assets and private R2 answer keys.

## Visual Designer Workflow

Start the local backend and frontend, then open:

```text
http://127.0.0.1:5173/prepare-document
http://127.0.0.1:5173/exam-designer
```

In the designer:

1. Use the `Document upload and preprocess` tab to upload a PDF.
2. Choose an existing current document, or choose a new PDF and optional document id.
3. Click `Process document`.
4. The backend runs OpenDataLoader only and saves editable files under `documents/<document-id>/`.
5. The document opens in the reader preview without generated representations.
6. Use the `Paragraph matching` tab to click chunks on or off for each paragraph and preview the selected text. Use `Add above` and `Add below` when a paragraph needs to be inserted.
7. Click `Save document` to persist paragraph/chunk mappings with ordered paragraph ids.
8. Use the `Representation generation` tab to edit prompts, colors, opacity, and visibility.
9. Click `Apply` to save prompts and generate representations with progress shown in the tab.
10. Click `Save representations` to write prompt definitions and generated representation data to separate files.

Successful document and representation saves show an alert with the files written under `documents/<document-id>/`.

The designer intentionally does not generate representations during upload. It waits until prompts are edited and the designer explicitly starts generation.

Use `/exam-designer` to create, edit, and remove exam settings. Each exam selects one prepared document, chooses countdown or stopwatch timing, selects visible representation names with checkboxes, and stores multiple-choice questions with choices and answer indexes. Question ids are assigned automatically.

## Script Workflow

1. Move a PDF into `pdfs/<document-id>/source.pdf`.
2. Edit `config/default_document_config.json` if needed.
3. Run extraction:

```powershell
backend\.venv\Scripts\python.exe human-study\scripts\1_extract_document.py --document <document-id>
```

4. Optionally edit:

```text
documents/<document-id>/chunks.json
documents/<document-id>/paragraphs.json
documents/<document-id>/document.json
documents/<document-id>/representation-prompts.json
documents/<document-id>/representations.json
```

You can also edit these files visually from `/prepare-document`.

5. Run representation generation:

```powershell
backend\.venv\Scripts\python.exe human-study\scripts\2_generate_representations.py --document <document-id>
```

6. Edit or create exam settings under `exams/`, or use `/exam-designer`.
7. Edit `scheduler.json`.
8. Export for Cloudflare:

```powershell
backend\.venv\Scripts\python.exe human-study\scripts\3_export_cloudflare.py
```

9. Build and preview:

```powershell
run-test.bat
```

Open `http://127.0.0.1:8788/study` or `http://127.0.0.1:8788/exam`.

Exam submissions allow unanswered questions. Successful submission shows a completion notice and ends the session.

## Important

Public exports never include answer keys. Private answer keys are written to `private-r2/` for upload to R2.
