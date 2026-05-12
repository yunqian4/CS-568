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

## Basic Workflow

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
```

5. Run representation generation:

```powershell
backend\.venv\Scripts\python.exe human-study\scripts\2_generate_representations.py --document <document-id>
```

6. Edit or create exam settings under `exams/`.
7. Edit `scheduler.json`.
8. Export for Cloudflare:

```powershell
backend\.venv\Scripts\python.exe human-study\scripts\3_export_cloudflare.py
```

9. Build and preview:

```powershell
run-test.bat
```

Open `http://127.0.0.1:8788/study`.

## Important

Public exports never include answer keys. Private answer keys are written to `private-r2/` for upload to R2.
