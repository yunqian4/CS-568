# PDFReader Plan

## Goal

Deliver a web-based PDF reader prototype that accepts PDF upload or URL input, opens a reader after confirmation, parses PDFs through replaceable backend providers, builds a zoomable document tree from that output, and renders modular frontend overlays with readable block representations.

## Current Product Definition

- The landing page supports:
  - local PDF upload
  - remote PDF URL import
  - parser/provider choice between `opendataloader`, `native`, `docling`, and `grobid`
  - optional LLM-generated keywords and summaries
- The backend parser is a replaceable module with multiple provider options.
- Uploaded or imported PDFs are stored in content-addressed folders under `dat/temp/<sha256>/`.
- The file hash is the stable document id and storage directory name.
- Each provider must return text chunks with enough geometry to place overlays in the reader.
- The backend constructs a semantic zoomable document tree from parser output.
- The zoomable document is a tree of sections, subsections, and paragraph leaves.
- Each paragraph leaf in the zoomable document represents one semantic paragraph and may map to one or more text chunks.
- The native parser should identify raw text blocks and merge consecutive text blocks into one logical block when they belong to the same reading flow.
- The native semantic parser should infer title, section headings, subsection headings, and paragraph leaves from layout and typography heuristics.
- The GROBID parser should use GROBID TEI body content to ignore paper front matter and references, then align retained body content back to PyMuPDF chunks for precise overlays.
- GROBID should be integrated behind the backend provider boundary with backend health checks and optional local Docker startup.
- The OpenDataLoader PDF parser should use local JSON output for semantic element types, reading order, and bounding boxes.
- The default pipeline is OpenDataLoader with LLM semantic grouping enabled.
- Two-column papers are a primary target for the native parsing path.
- The reader displays the PDF in-browser and overlays semantic chunk or block bounds.
- The reader supports PDF-style zoom controls; the canvas, selectable text layer, chunk bounds, and representations scale together.
- Each block owns a set of representations such as keywords, summaries, and later derived variants.
- Optional LLM representation generation fills qualifying paragraph leaf blocks progressively; placeholders are reserved for non-LLM mode only.
- LLM representation definitions are generic prompt records with `name`, `prompt`, `background_color`, `background_opacity`, and `enabled`.
- Default representation backgrounds are dark and fully opaque.
- Users can edit default keyword and summary prompts, add custom representation prompts, choose background colors and opacity, and submit prompt changes to restart representation generation for the current cached PDF.
- Prompt, color, and opacity settings are browser-session scoped by default, with explicit cookie save/load/clear controls.
- LLM keywords are generated above a configurable short-block threshold, defaulting to `4` words.
- LLM summaries are generated above a configurable long-block threshold, defaulting to `35` words, and target a configurable word-count ratio, defaulting to `0.15`.
- Users may provide a request-scoped OpenAI API key or rely on the backend default `OPENAI_API_KEY`.
- If the default OpenDataLoader LLM pipeline has no server key, the frontend requires a request key before import.
- Generated representations are delivered progressively by polling after the reader opens.
- A block may map to multiple overlays.
- A modular mapping rule determines which block drives which overlay and which representation is shown in that overlay.
- Paragraph-level chunking is the target granularity.
- The UI should favor responsive scrolling and avoid blur-heavy visuals.

## Implemented Decisions

- Keep both parser paths:
  - `native` parser
  - `docling` parser
  - optional `grobid` paper parser
  - optional `opendataloader` parser
- The native parser now uses PyMuPDF block extraction instead of the old pdfplumber line grouping path.
- Treat parser/provider integration as a replaceable contract rather than hard-coded logic.
- Treat zoomable document construction as a separate step from raw PDF parsing.
- Preserve the ability for one paragraph block to reference multiple source text chunks.
- Raw parser providers now return chunk geometry through a shared provider contract before document construction.
- Consecutive native text blocks are now merged into one paragraph block with column-aware ordering heuristics.
- Semantic parsing now classifies layout units into section headings, subsection headings, and paragraph leaves.
- The zoomable document tree now uses semantic hierarchy instead of page nodes.
- The GROBID provider now uses a backend service wrapper, calls GROBID REST internally, extracts TEI body headings and paragraphs, excludes front/back matter, and filters PyMuPDF chunks to aligned body content.
- `GROBID_URL` can override the default local GROBID endpoint.
- `GROBID_AUTO_START=1` lets the backend start a local Docker container when GROBID is not already ready.
- The default managed Docker image is the lightweight `grobid/grobid:0.9.0-crf`; it can be changed with `GROBID_DOCKER_IMAGE`.
- The OpenDataLoader provider converts PDFs to JSON, maps OpenDataLoader `heading`, `paragraph`, `caption`, and `list item` elements into the shared chunk contract, and preserves those provider elements as pre-segmented semantic chunks.
- OpenDataLoader bounding boxes are converted from PDF points `[left, bottom, right, top]` into normalized frontend overlay coordinates.
- The semantic pipeline now records `opendataloader-semantic-json-v1` when OpenDataLoader semantic JSON drives the zoomable tree.
- The backend now persists each PDF under `dat/temp/<sha256>/source.pdf`, stores a `manifest.json`, and writes provider-specific parsed API payloads under `providers/<provider>/document.json`.
- Same-PDF imports reuse cached provider payloads only when the stored `cache_version` and parser/semantic `cache_profile` match the current implementation.
- Current parser cache profiles include the parser, semantic mode, and semantic LLM model; representation prompt settings are tracked separately so prompt edits do not force reparsing.
- The backend can enrich semantic paragraph blocks with OpenAI-backed keyword and summary representations after parsing and before writing provider `document.json`.
- Request-provided OpenAI keys are used only for the active import and are not persisted in manifests or document payloads.
- LLM representation settings include model, keyword minimum words, summary minimum words, summary ratio, and maximum keyword count.
- The default LLM representation model comes from `OPENAI_REPRESENTATION_MODEL` in `backend/.env` or the process environment.
- If no model is configured in env and the request does not provide one, the backend falls back to `gpt-5-nano`.
- LLM representation calls run with bounded parallelism controlled by `OPENAI_REPRESENTATION_PARALLELISM`, defaulting to `2` and clamped from `1` to `8`.
- OpenAI semantic and representation calls use `OPENAI_REQUEST_TIMEOUT_SECONDS` and `OPENAI_REQUEST_RETRIES`, defaulting to a 300 second read timeout and three transient retries for timeouts, rate limits, and server errors.
- The backend now loads default environment values from `backend/.env` and then `.env` while preserving any variables already set in the process environment.
- Local env files are ignored by git, with `backend/.env.example` kept as the backend template.
- OpenDataLoader LLM semantic parsing stores normalized chunk input at `providers/opendataloader/llm/semantic-input.json` and validated semantic output at `providers/opendataloader/llm/semantic.json`.
- LLM semantic grouping is allowed to group existing chunks, set section paths, assign roles, and mark ignored chunks; geometry remains sourced from parser chunks.
- OpenDataLoader LLM semantic input preserves provider chunk reading order, records a `reading_order` index, and validates paragraph output in that order rather than using bounding-box sorting.
- LLM semantic grouping is instructed not to split a single sentence across paragraphs, and backend postprocessing merges adjacent same-section groups when the sentence boundary was split.
- LLM semantic grouping now instructs the model to check sentence completeness before ending a paragraph and to merge adjacent continuation chunks into one paragraph group.
- Public LLM paragraph block IDs use stable `paragraph-000x` labels, while source chunk IDs remain visible separately in development overlays.
- LLM semantic grouping now sends bounded chunk windows rather than one whole-document prompt, and recursively splits any window that returns an incomplete `max_output_tokens` response.
- Per-window semantic inputs and outputs are cached under `providers/opendataloader/llm/semantic-windows/`.
- Semantic chunking can still take time because OpenDataLoader conversion and LLM semantic windows run before the reader can open.
- LLM representation generation is now progressive: import returns after parsing/semantic grouping, and per-block/per-kind jobs write cached files under `providers/<provider>/llm/representations/<block_id>/<kind>.json`.
- Representation jobs now mark an active job as `running` before the OpenAI request and expose pending/running/failed counts to the frontend.
- Representation jobs are executed through a bounded worker pool rather than a fully synchronized one-call-at-a-time loop.
- Representation OpenAI calls use compact per-block/per-kind prompts, sending only the paragraph text and receiving only `{"k":[...]}` for keywords or `{"s":"..."}` for summaries.
- Representation calls retry once with a larger output-token budget if OpenAI reports `max_output_tokens`.
- Representation calls now use editable definitions. Keywords still return compact keyword arrays for chip compatibility, while other definitions return a string value.
- Generated block representations expose `value` and `background_color` in addition to legacy `text` or `items` compatibility fields.
- The backend exposes `POST /api/documents/{document_id}/representations/regenerate` to restart cached representation jobs without reparsing the PDF.
- Representation prompt or color changes reuse the cached parsed document and restart only the per-block representation jobs.
- LLM-mode imports clear heuristic placeholder block representations before returning, so the reader shows generated outputs only after jobs complete.
- Same-PDF cache reuse now retries pending or failed representation jobs when a usable request or server key is available.
- The frontend polls `GET /api/documents/{document_id}/representations?provider=<provider>` and merges completed representation results into reader state.
- Representation polling should not reload the PDF file, reset visible pages, or rebuild canvas state when the poll response has no new data.
- The reader shows a bottom-right LLM representation status chip for pending, complete, failed, and no-eligible-block cases.
- The reader header no longer shows provider/pipeline metadata, and a lower-right settings control toggles keyword and summary visibility.
- The lower-right settings panel also edits representation prompts, colors, opacity, visibility, custom definitions, cookie persistence, and regeneration credentials.
- The lower-right settings panel includes a paragraph ID toggle for development block/chunk labels, defaulting to hidden.
- Frontend overlays now estimate summary footprint, keyword chip footprint, and chunk region size to place each block representation on the best fitting source region without duplicate badges.
- Frontend representation badges are clipped inside the source chunk overlay, keyword chips can wrap onto separate lines, and keywords render below any same-block summary.
- Frontend keyword display now gives each keyword its own rounded chip background instead of one combined keyword badge.
- Frontend overlay typography now scales per overlay from the source region size and the currently visible summary/keyword set.
- Frontend overlays suppress placeholder chunk keyword fallbacks while LLM representations are enabled.
- Zoomable document construction now builds:
  - page-level chunk lists
  - semantic paragraph leaf blocks with representation sets
  - a tree with document -> section -> subsection -> paragraph nodes when subsection headings exist
- Chunk overlays should:
  - highlight chunk regions with a light yellow background
  - use a darker keyword chip for readability
  - use readable generated representation text with enough spacing between keywords and summaries
  - show visible chunk frames
- Overlay content should be modeled as representations attached to blocks, not as a single keyword-only field.
- Overlay selection logic should remain modular so the project can swap display rules without reworking block extraction.
- The frontend now builds overlay containers from blocks plus source chunks instead of rendering directly from chunk keywords.
- The default frontend overlay rule lives in a dedicated module and chooses which representation kind each overlay shows.
- Keyword chips should become more transparent as the cursor approaches a chunk and disappear when the cursor is inside the chunk box.
- Rendering should prefer lazy visible-page behavior instead of eager full-document rasterization.
- A real PDF text layer is the intended mechanism for selection and copy inside the reader.

## Known Open Work

- Extend block construction beyond the current one-block-per-chunk default when a provider surfaces multi-region paragraphs that should remain one logical block.
- Tune native merge heuristics against real papers so paragraph joins are aggressive enough for split blocks without collapsing distinct paragraphs.
- Tune heading classification heuristics against real papers so section and subsection detection do not overfit to synthetic layout cues.
- Decide how to represent deeper heading levels beyond the current section/subsection/paragraph tree.
- Add provider abstraction if LLM representation generation becomes a frequent workflow.
- Consider adding non-OpenAI LLM providers behind the same representation generation boundary.
- Tune current-document representation regeneration UX after testing with real request-scoped API keys and long PDFs.
- Consider replacing polling with SSE if representation latency or status visibility becomes a UX issue.
- Add recovery for pending in-process representation jobs after backend restart when request-scoped keys are unavailable.
- Tune overlay placement heuristics against representative PDFs so summary and keyword region assignment remains readable across narrow columns and multi-page blocks.
- Decide whether overlay rules stay fully frontend-side or should also be mirrored in backend responses for caching or deterministic rendering.
- PDF text selection inside the canvas/text-layer area may still need further fixing if the real `pdf.js` text layer does not fully receive selection input.
- Multi-column PDFs still need validation to ensure chunk boxes never span both columns incorrectly.
- Semantic chunk overlays may still diverge from exact PDF text geometry in complex layouts.
- Compare `native` vs `docling` on representative PDFs before deciding whether `docling` should remain optional or become the default provider.
- Compare `native` vs `grobid` on representative papers to evaluate author/reference removal and paragraph alignment quality.
- Compare `native`, `docling`, and `opendataloader` on representative two-column papers to evaluate reading order, heading hierarchy, and overlay coordinate quality.
- Decide whether the GROBID TEI hierarchy should directly drive the zoomable section tree instead of acting as a body-content filter before the current semantic builder.
- Decide whether table cells from OpenDataLoader should become dedicated table blocks instead of flattened text chunks.
- Formalize the "perfect pipeline" target: parser semantic elements -> normalized semantic chunks -> zoomable section tree -> paragraph blocks -> source chunk overlays -> fit-based representation placement per block.
- Add an explicit revocation/deletion API when the app needs to remove `dat/temp/<sha256>/` artifacts.
- Backend packaging still needs cleanup if editable installs are expected; `pip install -e .` currently fails due package discovery.

## Acceptance Criteria

- Users can import PDFs by file or URL and enter the reader after confirmation.
- The frontend defaults to OpenDataLoader with LLM semantic grouping enabled.
- Users can choose between `opendataloader`, `native`, and `docling` parsing paths.
- Users can choose the `grobid` parsing path when a local GROBID service is available or backend-managed startup is enabled.
- Users can choose the `opendataloader` parsing path when Java 11+ and `opendataloader-pdf` are installed.
- The OpenDataLoader provider should discover Java from `PATH`, `JAVA_HOME`, or common Windows JDK/JRE install directories before failing with a setup error.
- The backend exposes PDF parsing through a replaceable provider/module boundary.
- Each uploaded/imported file creates or reuses `dat/temp/<sha256>/` based on the PDF content hash.
- Source files, manifests, and parsed/generated provider artifacts are persisted in the hash directory.
- Current cache is reused for repeated same-PDF uploads, while older unversioned or mismatched cache artifacts are ignored.
- Optional LLM representation generation can use either a request-provided key or the backend `OPENAI_API_KEY`.
- User-provided API keys are not persisted in `dat/temp`, manifests, provider documents, or frontend route state.
- Default OpenDataLoader LLM import requires either a server default key or a user request key.
- OpenDataLoader LLM semantic output cannot reference unknown chunk IDs.
- OpenDataLoader LLM semantic paragraph order follows provider chunk reading order, including for two-column papers.
- OpenDataLoader LLM semantic input and output are cached in the provider temp folder.
- OpenDataLoader LLM semantic grouping can complete long documents through chunk-window retries when a single LLM response would exceed output limits.
- Leaf paragraph blocks above the configured keyword threshold receive generated keywords when LLM generation is enabled.
- Leaf paragraph blocks above the configured summary threshold receive generated summaries sized from the configured word-count ratio.
- Keyword and summary representations can arrive after the reader opens and are merged into overlays by polling.
- Placeholder keyword chips are not shown in LLM mode while representation jobs are pending or failed.
- Readers can toggle keyword and summary visibility without changing the underlying parsed document.
- Readers can edit representation prompt definitions, colors, and opacity in the current session.
- Readers can show or hide development paragraph/chunk ID labels from the reader settings panel.
- Readers can regenerate representations for a cached document without reparsing the PDF when an API key is available.
- Readers can opt in to cookie persistence for prompt and color settings.
- Each provider returns text chunks with locations that are sufficient for overlay placement.
- The backend builds a semantic zoomable document tree from parsed PDF output.
- The zoomable document exposes sections, subsections, and paragraph leaves.
- Each paragraph leaf block can map to one or more source text chunks.
- The native parser can merge consecutive text blocks in the same column flow into one logical block.
- The native semantic parser can infer section and subsection hierarchy for paper-like PDFs.
- The GROBID parser excludes authors, affiliations, and references by keeping only TEI body content.
- The OpenDataLoader parser preserves semantic JSON elements and maps their bounding boxes to frontend overlays.
- Multi-chunk paragraph blocks display each keyword or summary representation once on a predicted-fit source region, while all source chunk frames remain visible.
- Development overlays include a `block-label` representation on every text chunk with the owning block ID and chunk ID.
- Chunks in the same block are treated as one paragraph that shares block representations, and each non-development representation is placed in at most one chunk overlay.
- Semantic paragraphs should not split one sentence across two zoomable blocks.
- Each block can expose multiple representations such as keywords and summaries.
- A block may drive multiple overlays.
- Overlay-display rules are modular and can be replaced without changing the block data model.
- Chunk overlays remain paragraph-level and do not collapse across columns in multi-column layouts.
- Horizontal and vertical chunk positions are reliable enough for overlay visualization.
- Overlays remain visually readable and distinct.
- Scroll performance stays responsive and blur effects are not used.
- Text selection and copy work inside the PDF reader area.
- Keyword proximity fading works without interfering with text selection.

## Planning Policy

- Update this file during the conversation when requirements, design decisions, or open work materially change.
- Keep the plan concrete and implementation-facing.
- When `AGENTS.md` guidance changes, update this file too if the change affects scope, workflow, verification, or acceptance criteria.

## Run and Verification

Backend:

```powershell
backend\.venv\Scripts\python.exe -m uvicorn backend.app:app --reload
```

Frontend:

```powershell
npm.cmd run dev
```

Optional GROBID service:

```powershell
$env:GROBID_URL = "http://localhost:8070"
$env:GROBID_AUTO_START = "1"
```

Optional OpenDataLoader PDF provider:

```powershell
backend\.venv\Scripts\python.exe -m pip install opendataloader-pdf
$env:OPENDATALOADER_USE_STRUCT_TREE = "1"
$env:JAVA_HOME = "C:\Program Files\Eclipse Adoptium\jdk-25.0.2.10-hotspot"
```

Optional LLM representations:

```powershell
Copy-Item backend\.env.example backend\.env
```

Then fill in `backend/.env`:

```env
OPENAI_API_KEY=...
OPENAI_REPRESENTATION_MODEL=gpt-5-nano
OPENAI_REQUEST_TIMEOUT_SECONDS=300
OPENAI_REQUEST_RETRIES=3
```

Verification commands:

```powershell
cd backend
.\.venv\Scripts\python.exe -m unittest tests.test_pdf_ingest
```

```powershell
cd frontend
npm.cmd run build
```

Relevant API endpoints:

- `GET /api/llm/config`
- `POST /api/documents/upload?provider=opendataloader`
- `POST /api/documents/from-url`
- `GET /api/documents/{document_id}/representations?provider=<provider>`
- `POST /api/documents/{document_id}/representations/regenerate`

## Working Conventions

- Keep this file aligned with `AGENTS.md`.
- Record major architecture changes here once they become decided or implemented.
- Keep open work explicit enough that the next change can start from this file without rediscovering context.

## Definition Of Done

- The requested behavior is implemented against the current parser/block/overlay architecture.
- Relevant backend tests and frontend build checks pass, or any missing verification is stated explicitly.
- `AGENTS.md` and `PLANS.md` are updated when the task changes requirements, structure, commands, constraints, or verification expectations.
- Remaining risks and follow-up work are reflected in `Known Open Work` when they matter.
