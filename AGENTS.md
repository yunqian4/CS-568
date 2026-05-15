# PDFReader Agent Guide

## Repo Layout

- `backend/`: FastAPI app, PDF parsing, zoomable document construction, tests.
- `backend/core/ingest/`: parser providers, provider contracts, and document builders.
- `backend/core/models/`: serialized API models for chunks, blocks, and zoomable nodes.
- `backend/core/representations/`: block representation helpers such as keywords and summaries.
- `backend/tests/`: backend unit tests.
- `frontend/src/`: React app source.
- `frontend/src/components/`: landing page, reader page, and PDF viewer components.
- `frontend/src/components/DesignerPage.jsx`: local prepare-document route with tabs for upload/preprocess, paragraph matching, and prompt-driven representation generation.
- `frontend/src/components/ExamDesignerPage.jsx`: local exam-designer route for creating cached exam JSON, timer mode, multiple-select visible representation conditions, and multiple-choice questions.
- `frontend/src/components/ExamPage.jsx`: backend-free Cloudflare-compatible exam runner that samples exported exam settings.
- `frontend/src/overlays/`: overlay-building logic and replaceable overlay rules.
- `frontend/public/study-cache/`: static cached-study assets copied into the Cloudflare Pages build.
- `functions/api/study/`: Cloudflare Pages Functions for cached-study scoring and result capture.
- `backend/scripts/prepare_human_eval_document.py`: prepares one PDF and its generated representations from a JSON config.
- `human-study/`: canonical designer workspace for PDFs, editable intermediates, exam JSON, scheduler JSON, staged scripts, and private R2 answer-key exports.
- `scripts/export_study_cache.py`: compatibility wrapper for the `human-study` Cloudflare exporter.
- `human-eval.md`: end-to-end guide for preparing and deploying human-evaluation tests.
- `dat/temp/`: content-addressed upload/import artifact folders keyed by PDF SHA-256.
- `backend/.env.example`: template for local backend environment defaults.
- `.gitignore`: excludes local env files, build output, virtualenvs, dependencies, and `dat/temp`.
- `AGENTS.md`: repo operating guide.
- `PLANS.md`: working requirements, decisions, open work, and verification policy.

## How To Run

From the repo root, start the backend:

```powershell
backend\.venv\Scripts\python.exe -m uvicorn backend.app:app --reload
```

From `frontend/`, start the frontend dev server:

```powershell
npm.cmd run dev
```

Local study document designer:

```powershell
# Run backend and frontend dev servers, then open:
http://127.0.0.1:5173/prepare-document
http://127.0.0.1:5173/exam-designer
```

Optional GROBID provider:

- The `grobid` parser is integrated through a backend service wrapper because GROBID is a JVM service exposed by its REST API.
- Default service URL: `http://localhost:8070`.
- Override with `GROBID_URL` when needed.
- Set `GROBID_AUTO_START=1` to let the backend start a local Docker container when the service is not already running.
- Optional Docker settings: `GROBID_DOCKER_IMAGE`, `GROBID_CONTAINER_NAME`, `GROBID_DOCKER_PORT`, and `GROBID_STARTUP_TIMEOUT_SECONDS`.

Optional OpenDataLoader PDF provider:

- The `opendataloader` parser uses OpenDataLoader PDF JSON output for semantic element types, reading order, and bounding boxes.
- It requires Java 11+ and the `opendataloader-pdf` Python package.
- The backend should first use `java` from `PATH`, then try common Windows JDK/JRE locations and `JAVA_HOME`.
- Install the provider dependency with `backend\.venv\Scripts\python.exe -m pip install opendataloader-pdf`.
- `OPENDATALOADER_USE_STRUCT_TREE` defaults to enabled when the installed package supports tagged PDF extraction.

Optional LLM representations:

- The backend can generate block keywords and summaries with an OpenAI-compatible Responses API call.
- Users may provide a request-scoped API key, or the backend may use `OPENAI_API_KEY` as the default key.
- `OPENAI_REPRESENTATION_MODEL` from `backend/.env` or the process environment is the default representation model when the request does not specify one.
- If no env value exists, representation generation falls back to `gpt-5-nano`.
- `OPENAI_REPRESENTATION_PARALLELISM` controls bounded parallel representation calls; default `2`, clamped from `1` to `8`.
- `OPENAI_REQUEST_TIMEOUT_SECONDS` controls OpenAI HTTP read timeout for semantic and representation calls; default `300`, clamped from `10` to `600`.
- `OPENAI_REQUEST_RETRIES` controls transient OpenAI timeout, transport, rate-limit, and server-error retries; default `3`, clamped from `0` to `5`.
- The backend loads local defaults from `backend/.env` and then `.env` without overriding already-set process environment variables.
- Use `backend/.env.example` as the template for local backend secrets.
- User-supplied API keys must never be written to `dat/temp`, `manifest.json`, provider `document.json`, logs, or frontend state beyond the active request.
- The default frontend pipeline is OpenDataLoader with LLM semantic grouping and progressive LLM representations enabled.
- If no server default key exists for the default LLM pipeline, the frontend must require a request key before import.
- Representation definitions are generic user-editable prompt records with `{ name, prompt, background_color, background_opacity, enabled }`.
- Default representation definitions are `keywords` and `summary`; users may edit or disable them during a browser session.
- Default representation backgrounds are dark with full opacity; prompt, color, and opacity edits are session-scoped by default. Cookie persistence is opt-in from the reader settings UI.
- Generated representation payloads should expose a string `value` and `background_color`. Keyword items may remain as compatibility display data, but the canonical generated content is the string value.

## Build, Test, And Lint

Frontend build:

```powershell
cd frontend
npm.cmd run build
```

Backend unit test:

```powershell
cd backend
.\.venv\Scripts\python.exe -m unittest tests.test_pdf_ingest
```

Frontend preview build:

```powershell
cd frontend
npm.cmd run preview
```

Extract editable text chunks and paragraph blocks for one study PDF:

```powershell
backend\.venv\Scripts\python.exe human-study\scripts\1_extract_document.py --document my-paper
```

Generate editable representations for one extracted study PDF:

```powershell
backend\.venv\Scripts\python.exe human-study\scripts\2_generate_representations.py --document my-paper
```

Export cached study assets:

```powershell
backend\.venv\Scripts\python.exe human-study\scripts\3_export_cloudflare.py
```

Cloudflare Pages local preview after building the frontend:

```powershell
npx wrangler pages dev frontend/dist
```

Cached study export, build, and local Pages preview:

```powershell
run-test.bat
```

Linting:

- No dedicated lint command is configured yet.
- Do not invent ad hoc lint tooling as part of routine work unless the task is to add and wire it properly.

## Product Intent

- Build a web-based PDF reader modeled after the sibling `ZoomableReader` project.
- The landing page must support either uploading a PDF or entering a PDF URL.
- After confirmation, the app should open the web reader for that PDF.
- The `/study` route is a controlled cached-study runner for Cloudflare Pages deployments without the FastAPI backend.
- The `/prepare-document` route is a local preparation tool for uploading and processing human-study PDFs, matching chunks to paragraphs, editing representation prompts, and explicitly generating representations before export.
- The `/exam-designer` route is a local exam-setting editor for reusable prepared documents.
- The `/exam` route is a backend-free cached exam runner for Cloudflare Pages.

## Engineering Conventions

- Keep code and comments concise.
- Add short module or file documentation when it improves readability.
- Prefer durable, practical changes over speculative abstraction.
- Preserve the replaceable parser/provider boundary in the backend.
- Keep zoomable document construction separate from raw parser output.
- Keep overlay-selection rules modular and replaceable.
- Prefer paragraph-level blocks and chunk mappings unless a task clearly requires finer granularity.
- Match existing local patterns before introducing new abstractions.

## PR Expectations

- Keep changes scoped to the task.
- Update tests when backend behavior, data contracts, or parsing behavior changes.
- Update user-facing copy when behavior or terminology changes.
- Update `AGENTS.md` and `PLANS.md` when requirements, architecture, commands, constraints, or open work materially change.
- Keep answer keys out of `frontend/public/study-cache`; public question files must not include `answer_index` or equivalent correct-answer fields.
- Call out any residual risks, missing tests, or follow-up work in the final summary.

## Backend Expectations

- The Python backend must treat PDF parsing as a replaceable module with multiple parser/provider options.
- Uploaded or imported PDFs must be stored under `dat/temp/<sha256>/`, where `<sha256>` is the file content hash.
- Each upload/import artifact folder should keep `source.pdf`, `manifest.json`, and provider-specific generated files under `providers/<provider>/`.
- Reuse same-PDF provider cache only when `cache_version` and `cache_profile` match the current implementation; ignore artifacts from older implementations.
- Keep parser/semantic cache identity separate from representation prompt settings, so editing representation prompts reuses the parsed document and restarts only per-block representation jobs.
- Same-PDF cache reuse should restart pending or failed LLM representation jobs when a usable request or server API key is available.
- Persist parsed document JSON and generated representations so future summaries or revocation workflows can use the same artifact folder.
- Each parser/provider must identify text chunks plus their PDF locations.
- The backend should build a semantic zoomable document tree from parser output.
- The zoomable document should be a tree of sections, subsections, and paragraph leaves.
- Each paragraph leaf block should represent one semantic paragraph and may map to one or more text chunks.
- The native parser should favor block-wise extraction that works for academic papers and two-column layouts.
- Consecutive text blocks in the same reading flow should be concatenated into one logical paragraph block only when they appear to split one sentence across chunks.
- The native semantic parser should infer document title, section headings, subsection headings, and paragraph leaves from layout and typography heuristics.
- Paragraph-level chunking is sufficient; line-level granularity is not required unless a parser needs line data internally.
- Keep the native parser path available.
- Support an alternative Docling-based segmentation/provider path.
- Support an optional GROBID-backed paper parser path for scholarly PDFs.
- The GROBID provider should use backend-owned service management for health checks and optional local Docker startup instead of requiring frontend code or users to call GROBID directly.
- The GROBID provider should use GROBID TEI body content to exclude front matter such as authors and affiliations, and back matter such as references.
- The GROBID provider should keep PyMuPDF as the geometry source so paragraph overlays still map to precise PDF chunks.
- Support an optional OpenDataLoader PDF parser path for local semantic JSON extraction with multi-column reading order and bounding boxes.
- The OpenDataLoader provider should preserve provider-level semantic elements instead of re-merging them as raw text blocks.
- The default reader pipeline should use OpenDataLoader chunks plus LLM semantic grouping when credentials are available.
- LLM semantic grouping may group chunks, assign section paths, mark ignored chunks, and assign roles, but it must not create geometry or unknown chunk IDs.
- OpenDataLoader LLM semantic grouping must preserve the provider chunk reading order and must not re-sort chunks by bounding-box coordinates before grouping.
- LLM semantic grouping should keep chunks from the same sentence in one paragraph group; deterministic postprocessing may merge adjacent same-section groups only when a sentence was split across chunks.
- LLM semantic grouping should explicitly check paragraph sentence completeness before ending a paragraph, so adjacent continuation chunks are grouped into the same paragraph block without merging separate sentences.
- Public LLM paragraph block IDs should use stable `paragraph-000x` labels instead of embedding provider chunk IDs or free-form LLM paragraph IDs.
- Store OpenDataLoader LLM semantic inputs and outputs under `providers/opendataloader/llm/semantic-input.json` and `providers/opendataloader/llm/semantic.json`.
- Split OpenDataLoader semantic grouping into bounded chunk windows, and recursively split a window if OpenAI returns an incomplete `max_output_tokens` response.
- Keep OpenDataLoader semantic windows small enough to avoid long Responses API read timeouts.
- Expect OpenDataLoader semantic chunking to be slower than native parsing because it may include Java conversion plus multiple sequential LLM window requests.
- Support optional LLM-backed representation generation for semantic paragraph leaf blocks.
- Support user-customizable prompt definitions for LLM-backed block representations.
- Keywords should be generated only when a leaf block has at least the configured minimum word count.
- Summaries should be generated only when a leaf block has at least the configured summary minimum word count.
- Default LLM representation thresholds are `20` words for keywords and `35` words for summaries; the backend does not generate any representation for blocks with 20 or fewer words.
- Summary target length should scale from the block word count using the configured ratio, defaulting to about `0.15`.
- Generate paragraph representations progressively after import, cache each block/kind result under `providers/<provider>/llm/representations/<block_id>/<kind>.json`, and expose polling status through the backend.
- When LLM representations are enabled, do not return heuristic placeholder block representations while jobs are pending.
- Representation generation should use compact per-block/per-kind prompts: keyword calls return only `{"k":[...]}` and summary calls return only `{"s":"..."}`.
- Representation jobs should run with bounded parallelism instead of one fully synchronized OpenAI call at a time.
- Representation job status should distinguish pending, running, complete, and failed jobs.
- Representation regeneration should be available for an already cached document without reparsing the PDF.
- LLM representation metadata may be persisted, but API keys and secrets must not be persisted.
- Keep parser selection, chunk-to-block mapping, and zoomable document construction modular so additional providers can be added without rewriting the reader contract.
- Cached study exports are generated offline from `dat/temp/<sha256>/providers/opendataloader/document.json` and `source.pdf`; Cloudflare Pages must not run OpenDataLoader, Java, FastAPI, or live OpenAI calls.
- The human-study scripts should use the existing backend parser and representation pipeline, keep editable intermediates under `human-study/documents/<document-id>/`, and avoid persisting request API keys in public exports.
- The backend exposes local `GET` and `POST` endpoints under `/api/human-study/documents` so `/prepare-document` can upload/process PDFs, serve source PDFs, read and persist `human-study/documents/<document-id>/document.json`, `chunks.json`, `paragraphs.json`, `config.json`, and `representation-prompts.json`, and explicitly generate representations after prompt edits.
- The backend exposes local `GET`, `POST`, and `DELETE` endpoints under `/api/human-study/exams` so `/exam-designer` can list, save, and remove exam JSON files under `human-study/exams/`.
- The prepare-document upload path runs OpenDataLoader only; it must not run LLM paragraph matching or auto-generate LLM representations before the designer edits and submits representation prompts.
- Cached study result capture uses Pages Functions and an R2 binding named `STUDY_RESULTS`.
- Study submissions should be written as one immutable JSON object under `study-results/<study_id>/<yyyy-mm-dd>/<session_id>.json` instead of appending to a shared file.
- Private answer keys should be uploaded to R2 under `study-private/answer-keys/<question_set_id>.json`.

## Frontend Expectations

- Render the PDF in a web reader.
- Render `/study` as a separate cached-study route while preserving `/` as the upload/debug reader.
- Render `/prepare-document` as a separate local designer route while preserving `/` and `/study`.
- Render `/exam-designer` as a local-only exam JSON editor while preserving existing routes.
- Render `/exam` as a static cached exam runner that does not depend on FastAPI.
- The designer route should let designers select an existing prepared document or upload a new PDF, process uploads with OpenDataLoader only, reuse the reader interface for preview, force chunk ID labels on every chunk, and provide controls for matching paragraph blocks to chunks with click-to-toggle multi-selection.
- The designer route should support adding paragraphs above or below existing paragraph blocks while matching chunks.
- The designer route should display paragraph IDs as ordered view labels and assign saved paragraph IDs in order as `paragraph-1`, `paragraph-2`, and so on when the designer clicks Save document.
- The paragraph matching pane should show a text preview from the currently selected chunks for each paragraph.
- The designer route should save document edits to `document.json`, `chunks.json`, and `paragraphs.json`, and save representation prompt edits to a separate `representation-prompts.json` file plus compatible `config.json` updates through the local backend when available.
- The designer route should generate document representations only after the designer edits prompts and clicks the explicit Apply generation control.
- The designer Apply control should request at most four concurrent OpenAI representation calls and show generation progress while jobs run.
- The designer Save representations control should write prompt definitions and generated representation data to separate files and alert the designer with the saved paths.
- If the local backend is unavailable, the designer route may fall back to exported static assets and JSON downloads.
- The study route should assign one exam setting from `frontend/public/study-cache/manifest.json` using the exported global scheduler.
- The study route should use an anonymous browser participant id, collect pre-questionnaire responses, display the assigned cached PDF, collect exam answers, collect post-questionnaire responses, then submit to `/api/study/score-submit`.
- The exam route should sample one exported exam setting, wait for an explicit Start button, display only the configured representation kinds, hide paragraph/chunk id labels, and submit answers to `/api/study/score-submit`.
- Exam settings support `timing_mode` values `countdown` and `stopwatch`; countdown exams auto-submit when the timer expires, and both modes show a submit button after the questions.
- Exam question IDs are assigned automatically from exam id and question order; designers should not manually edit question IDs.
- Exam submissions must allow unanswered questions and submit them as `selected_index: null`; after successful submission the UI should notify the participant and end the session.
- FastAPI provides a local-development mirror of `POST /api/study/score-submit`, but Cloudflare deployments use the Pages Function implementation.
- The reader should support normal PDF zoom in/out controls, with canvas, text layer, and overlays scaling together.
- Wide PDF pages should share one horizontal scroll context instead of each page having its own horizontal scrollbar.
- Overlay visible PDF regions with modular overlay containers.
- Each overlay container may present one or more block representations such as keywords, summaries, or similar derived views.
- Each block should own a set of representations, but a block may map to multiple overlays.
- The rule that determines which block drives which overlay and which representation is shown must be modular and replaceable.
- When one paragraph block maps to multiple text chunks, estimate summary size, keyword chip size, and region size before assigning which region displays each representation; keep source chunk frames visible without duplicate badges.
- Chunks in the same block are the same paragraph and share block representations; each non-development representation should be displayed in at most one source chunk overlay.
- Poll backend representation status after the reader opens and merge completed keyword/summary results into the displayed document.
- Representation polling must update overlays without reloading the PDF document or resetting visible-page state.
- Reader UI should expose LLM representation progress/status so pending, complete, failed, and no-eligible-block cases are visible.
- LLM representation failures should be visible in the reader and reported to the browser and backend consoles with available backend error details.
- The reader should provide controls for toggling representation visibility, including keywords and summaries.
- The reader settings should allow users to add prompt definitions, edit default prompts, edit colors and opacity, submit edited prompts for regeneration, reset defaults, and optionally save/load/clear cookie-backed settings.
- The reader settings should include a toggle for showing or hiding development paragraph/chunk ID labels.
- Development paragraph/chunk ID labels should default to hidden.
- Overlay each text chunk or block region with bounds.
- For development, show each text chunk's owning block and chunk ID as a `block-label` overlay representation.
- In non-LLM mode, placeholder keywords may be shown until richer representations are available.
- In LLM mode, do not fall back to placeholder keyword chips; show generated keywords and summaries only after polling receives them.
- Generated keyword and summary badges should use readable text size and enough spacing to distinguish multiple representations.
- Overlay representation font size should be calculated from the source region size and the currently visible representations for that block or overlay.
- Keywords must not render above a summary for the same block; when they share an overlay, render the summary first and keywords below it.
- Each generated keyword should render as its own rounded chip/background rather than sharing one combined keyword badge.
- Generated representation badges must remain contained inside the source chunk overlay; keyword badges may wrap with line breaks between keywords when there are many terms.
- Keep the chunk highlight light yellow.
- Use a darker keyword chip/background so keywords stay readable.
- Visualize chunk frames/bounds.
- Fade keyword chips as the cursor approaches a chunk and hide them when the cursor is inside the chunk box.
- Use a real PDF text layer so text selection and copy can work inside the reader.

## Constraints And Do-Not Rules

- Do not use blur-heavy effects such as `backdrop-filter` or similar visual blur features.
- Prefer lazy or visible-page rendering over eager full-document rendering.
- Keep scroll behavior responsive on both the landing page and the reader.
- Do not keep `AGENT.md` as a parallel source of truth after its useful content is migrated.
- Do not bypass the parser/provider contract by hard-coding provider-specific logic into unrelated layers.
- Do not couple overlay rendering directly to one representation type when the block model can carry multiple representations.
- Do not add new commands or workflows to this guide unless they actually exist in the repo.
- Do not regress two-column paper parsing by flattening reading order across columns without explicit column handling.
- Do not require GROBID for the default native parser path; it must remain optional.
- Do not make the frontend call GROBID directly; all parser providers must go through the backend provider boundary.
- Do not require LLM calls for explicitly non-LLM provider paths; generated representations must remain optional outside the default OpenDataLoader LLM pipeline.
- Do not persist user-provided LLM API keys or expose them in returned document payloads.
- Do not put private answer keys under `frontend/public` or any other served static asset folder.
- Do not depend on the FastAPI `/api/documents/*` endpoints from the `/study` route.

## Done Means

Work is done when:

- the requested behavior is implemented coherently across backend and frontend where applicable
- tests and builds relevant to the change pass, or any gaps are explicitly called out
- docs are updated when the task changes requirements, structure, workflow, or constraints
- the result matches the parser/block/overlay architecture described in `PLANS.md`

## Verification

Before closing work, verify the relevant parts of the project:

- backend parsing and document-shape changes: run the backend unit test
- frontend UI or data-flow changes: run the frontend build
- end-to-end reader changes: run backend and frontend together and smoke-test PDF import plus reader rendering when practical

## File Policy

- `AGENTS.md` is the canonical agent instruction file for this repo.
- `PLANS.md` is the working plan and requirements log.
- In future work, always update `AGENTS.md` and `PLANS.md` when necessary. This includes requirement changes, architectural decisions, command changes, verification changes, constraints, and newly identified open work.
