import { useEffect, useMemo, useState } from 'react';
import ReaderPage from './ReaderPage';
import {
  defaultCustomRepresentationSettings,
  normalizeRepresentationSettings,
  resetRepresentationSettings,
  toRepresentationDefinitions,
} from '../representationSettings';

const MANIFEST_URL = '/study-cache/manifest.json';
const BACKEND_DOCUMENTS_URL = '/api/human-study/documents';
const DESIGNER_TABS = [
  { id: 'upload', label: 'Document upload and preprocess' },
  { id: 'matching', label: 'Paragraph matching' },
  { id: 'representations', label: 'Representation generation' },
];

export default function DesignerPage() {
  const [documents, setDocuments] = useState([]);
  const [sourceMode, setSourceMode] = useState('backend');
  const [selectedDocumentId, setSelectedDocumentId] = useState('');
  const [uploadFile, setUploadFile] = useState(null);
  const [uploadDocumentId, setUploadDocumentId] = useState('');
  const [llmApiKey, setLlmApiKey] = useState('');
  const [llmModel, setLlmModel] = useState('');
  const [isProcessing, setIsProcessing] = useState(false);
  const [isGenerating, setIsGenerating] = useState(false);
  const [generationProgress, setGenerationProgress] = useState(null);
  const [baseDocument, setBaseDocument] = useState(null);
  const [pages, setPages] = useState([]);
  const [blocks, setBlocks] = useState([]);
  const [config, setConfig] = useState({});
  const [representationSettings, setRepresentationSettings] = useState(resetRepresentationSettings);
  const [activeTab, setActiveTab] = useState('upload');
  const [statusMessage, setStatusMessage] = useState('');
  const [errorMessage, setErrorMessage] = useState('');

  useEffect(() => {
    let cancelled = false;

    async function loadIndex() {
      setErrorMessage('');
      try {
        const payload = await fetchJson(BACKEND_DOCUMENTS_URL);
        const editableDocuments = (payload.documents ?? []).filter((item) => item.has_document);
        if (editableDocuments.length) {
          const requestedId = requestedDocumentId();
          const firstDocument = editableDocuments.find((item) => item.document_id === requestedId) ?? editableDocuments[0];
          if (cancelled) return;
          setDocuments(editableDocuments);
          setSourceMode('backend');
          await loadBackendDocument(firstDocument.document_id, { cancelled });
          return;
        }
      } catch {
        // The designer route can still inspect exported static cache files.
      }

      try {
        const manifest = await fetchJson(MANIFEST_URL);
        const publicDocuments = manifest.documents ?? [];
        if (!publicDocuments.length) {
          if (!cancelled) {
            setDocuments([]);
            setSourceMode('backend');
          }
          return;
        }
        const requestedId = requestedDocumentId();
        const firstDocument = publicDocuments.find((item) => item.document_id === requestedId) ?? publicDocuments[0];
        if (cancelled) return;
        setDocuments(publicDocuments);
        setSourceMode('public');
        await loadPublicDocument(firstDocument, { cancelled });
      } catch (error) {
        if (!cancelled) {
          setErrorMessage(error.message || 'Unable to load designer documents.');
        }
      }
    }

    loadIndex();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    async function loadLlmConfig() {
      try {
        const payload = await fetchJson('/api/llm/config');
        if (!cancelled && payload.default_model) {
          setLlmModel(String(payload.default_model));
        }
      } catch {
        // The field remains editable when the backend is not available.
      }
    }
    loadLlmConfig();
    return () => {
      cancelled = true;
    };
  }, []);

  const flattenedChunks = useMemo(() => flattenChunks(pages), [pages]);
  const previewDocument = useMemo(() => {
    if (!baseDocument || !selectedDocumentId) {
      return null;
    }
    return buildDocumentPayload({
      baseDocument,
      blocks,
      designerMode: true,
      documentId: selectedDocumentId,
      pages,
      pdfUrl: baseDocument.pdf_url,
      representationSettings,
    });
  }, [baseDocument, blocks, pages, representationSettings, selectedDocumentId]);

  async function loadBackendDocument(documentId, guard = {}) {
    const payload = await fetchJson(`${BACKEND_DOCUMENTS_URL}/${encodeURIComponent(documentId)}`);
    if (guard.cancelled) return;
    setEditableDocument({
      chunks: payload.chunks,
      config: payload.config,
      document: payload.document,
      documentId,
      paragraphs: payload.paragraphs,
      representationPrompts: payload.representation_prompts,
      mode: 'backend',
    });
  }

  async function loadPublicDocument(entry, guard = {}) {
    const document = await fetchJson(entry.document_url);
    if (guard.cancelled) return;
    setEditableDocument({
      chunks: { pages: document.pages ?? [] },
      config: configFromDocument(document),
      document: {
        ...document,
        pdf_url: document.pdf_url || `/study-cache/documents/${entry.document_id}/source.pdf`,
      },
      documentId: entry.document_id,
      paragraphs: { blocks: document.blocks ?? [] },
      representationPrompts: null,
      mode: 'public',
    });
  }

  function setEditableDocument({ chunks, config: nextConfig, document, documentId, paragraphs, representationPrompts, mode }) {
    setSelectedDocumentId(documentId);
    setBaseDocument(document);
    setPages(normalizePages(chunks?.pages ?? document.pages ?? []));
    setBlocks(mergeParagraphBlocks(document.blocks ?? [], paragraphs?.blocks ?? []));
    setConfig(nextConfig ?? {});
    setRepresentationSettings(settingsFromDefinitions(
      representationPrompts?.representations
        ?? nextConfig?.llm?.representations
        ?? document.metadata?.representation_definitions
        ?? [],
    ));
    setSourceMode(mode);
    setStatusMessage('');
    window.history.replaceState({}, '', `/prepare-document/${encodeURIComponent(documentId)}`);
  }

  async function handleDocumentChange(documentId) {
    const entry = documents.find((item) => item.document_id === documentId);
    if (!entry) return;
    setStatusMessage('');
    setErrorMessage('');
    try {
      if (sourceMode === 'backend') {
        await loadBackendDocument(documentId);
      } else {
        await loadPublicDocument(entry);
      }
    } catch (error) {
      setErrorMessage(error.message || 'Unable to load document.');
    }
  }

  async function processUploadedDocument(event) {
    event.preventDefault();
    if (!uploadFile) {
      setErrorMessage('Choose a PDF first.');
      return;
    }

    setIsProcessing(true);
    setStatusMessage('Processing PDF with OpenDataLoader...');
    setErrorMessage('');
    try {
      const body = new FormData();
      body.append('file', uploadFile);
      if (uploadDocumentId.trim()) {
        body.append('document_id', uploadDocumentId.trim());
      }
      if (llmModel.trim()) {
        body.append('llm_model', llmModel.trim());
      }
      const response = await fetch(`${BACKEND_DOCUMENTS_URL}/upload`, {
        body,
        method: 'POST',
      });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.detail || 'Unable to process PDF.');
      }
      const documentId = payload.document.study_document_id || payload.document.document_id;
      setEditableDocument({
        chunks: payload.chunks,
        config: payload.config,
        document: payload.document,
        documentId,
        paragraphs: payload.paragraphs,
        representationPrompts: payload.representation_prompts,
        mode: 'backend',
      });
      setDocuments((current) => upsertDocument(current, {
        document_id: documentId,
        has_document: true,
        has_pdf: true,
        title: payload.document.title || payload.document.source_name || documentId,
      }));
      setUploadFile(null);
      setUploadDocumentId('');
      setStatusMessage('Document processed with OpenDataLoader. Match paragraphs before generating representations.');
      setActiveTab('matching');
    } catch (error) {
      setErrorMessage(error.message || 'Unable to process PDF.');
      setStatusMessage('');
    } finally {
      setIsProcessing(false);
    }
  }

  async function saveDocument() {
    if (!previewDocument || !selectedDocumentId) return;

    const savePayload = buildDocumentPayload({
      baseDocument: previewDocument,
      blocks,
      designerMode: false,
      documentId: selectedDocumentId,
      pages,
      pdfUrl: `/study-cache/documents/${selectedDocumentId}/source.pdf`,
      renumberParagraphs: true,
      representationSettings,
    });
    const chunks = buildChunksPayload(savePayload.pages);
    const paragraphs = buildParagraphsPayload(savePayload.blocks);

    if (sourceMode === 'backend') {
      try {
        const response = await fetch(`${BACKEND_DOCUMENTS_URL}/${encodeURIComponent(selectedDocumentId)}`, {
          body: JSON.stringify({ chunks, document: savePayload, paragraphs }),
          headers: { 'Content-Type': 'application/json' },
          method: 'POST',
        });
        const payload = await response.json();
        if (!response.ok) {
          throw new Error(payload.detail || 'Unable to save document.');
        }
        setBaseDocument({
          ...savePayload,
          pdf_url: designerPdfUrl(selectedDocumentId),
        });
        setBlocks(savePayload.blocks);
        const message = `Document saved.\n${formatSavedPaths(payload.saved_paths)}`;
        setStatusMessage('Document saved.');
        window.alert(message);
        return;
      } catch (error) {
        setStatusMessage(`Backend save failed. Downloaded JSON instead. ${error.message || ''}`.trim());
      }
    } else {
      setStatusMessage('Downloaded document JSON.');
    }

    downloadJson('document.json', savePayload);
    downloadJson('chunks.json', chunks);
    downloadJson('paragraphs.json', paragraphs);
  }

  async function saveRepresentations() {
    if (!selectedDocumentId) return;
    const definitions = toRepresentationDefinitions(representationSettings);

    if (sourceMode === 'backend') {
      try {
        const response = await fetch(`${BACKEND_DOCUMENTS_URL}/${encodeURIComponent(selectedDocumentId)}/representations`, {
          body: JSON.stringify({ document: previewDocument, representations: definitions }),
          headers: { 'Content-Type': 'application/json' },
          method: 'POST',
        });
        const payload = await response.json();
        if (!response.ok) {
          throw new Error(payload.detail || 'Unable to save representations.');
        }
        const message = `Representations saved.\n${formatSavedPaths(payload.saved_paths)}`;
        setStatusMessage('Representations saved.');
        window.alert(message);
        return;
      } catch (error) {
        setStatusMessage(`Backend save failed. Downloaded prompt JSON instead. ${error.message || ''}`.trim());
      }
    } else {
      setStatusMessage('Downloaded representation prompt JSON.');
    }

    downloadJson('representation-prompts.json', buildPromptPayload(selectedDocumentId, representationSettings));
    downloadJson('representations.json', buildRepresentationsPayload(selectedDocumentId, previewDocument));
  }

  async function generateRepresentations() {
    if (!previewDocument || !selectedDocumentId) return;
    if (sourceMode !== 'backend') {
      setStatusMessage('Start the local backend to generate representations.');
      return;
    }

    setIsGenerating(true);
    setGenerationProgress({
      completed_jobs: 0,
      failed_jobs: 0,
      pending_jobs: 0,
      running_jobs: 0,
      status: 'preparing',
      total_jobs: 0,
    });
    setStatusMessage('Saving prompts and generating representations...');
    setErrorMessage('');
    const stopProgressPolling = startRepresentationProgressPolling(selectedDocumentId);
    try {
      const savePayload = buildDocumentPayload({
        baseDocument: previewDocument,
        blocks,
        designerMode: false,
        documentId: selectedDocumentId,
        pages,
        pdfUrl: `/study-cache/documents/${selectedDocumentId}/source.pdf`,
        renumberParagraphs: true,
        representationSettings,
      });
      const saveResponse = await fetch(`${BACKEND_DOCUMENTS_URL}/${encodeURIComponent(selectedDocumentId)}`, {
        body: JSON.stringify({
          chunks: buildChunksPayload(savePayload.pages),
          document: savePayload,
          paragraphs: buildParagraphsPayload(savePayload.blocks),
        }),
        headers: { 'Content-Type': 'application/json' },
        method: 'POST',
      });
      const saveResult = await saveResponse.json();
      if (!saveResponse.ok) {
        throw new Error(saveResult.detail || 'Unable to save document before generation.');
      }

      const response = await fetch(`${BACKEND_DOCUMENTS_URL}/${encodeURIComponent(selectedDocumentId)}/representations/generate`, {
        body: JSON.stringify({
          api_key: llmApiKey.trim() || null,
          enabled: true,
          model: llmModel.trim() || null,
          openai_representation_parallelism: 4,
          representations: toRepresentationDefinitions(representationSettings),
        }),
        headers: { 'Content-Type': 'application/json' },
        method: 'POST',
      });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.detail || 'Unable to generate representations.');
      }
      setEditableDocument({
        chunks: payload.chunks,
        config: payload.config,
        document: payload.document,
        documentId: selectedDocumentId,
        paragraphs: payload.paragraphs,
        representationPrompts: payload.representation_prompts,
        mode: 'backend',
      });
      setStatusMessage('Representations generated.');
      setGenerationProgress(payload.document?.metadata?.llm_representations ?? null);
    } catch (error) {
      setErrorMessage(error.message || 'Unable to generate representations.');
      setStatusMessage('');
    } finally {
      stopProgressPolling();
      setIsGenerating(false);
    }
  }

  function startRepresentationProgressPolling(documentId) {
    let stopped = false;

    async function poll() {
      try {
        const payload = await fetchJson(`${BACKEND_DOCUMENTS_URL}/${encodeURIComponent(documentId)}/representations/status`);
        if (!stopped) {
          setGenerationProgress(payload);
        }
      } catch {
        // The first poll may run before job status exists; the Apply response still settles the UI.
      }
    }

    poll();
    const intervalId = window.setInterval(poll, 900);
    return () => {
      stopped = true;
      window.clearInterval(intervalId);
    };
  }

  function updateBlock(blockId, patch) {
    setBlocks((current) => current.map((block) => (
      block.block_id === blockId ? { ...block, ...patch } : block
    )));
  }

  function addParagraph() {
    setBlocks((current) => [
      ...current,
      emptyParagraph(current),
    ]);
    setActiveTab('matching');
  }

  function insertParagraph(blockId, placement) {
    setBlocks((current) => {
      const index = current.findIndex((block) => block.block_id === blockId);
      const insertionIndex = index === -1
        ? current.length
        : index + (placement === 'below' ? 1 : 0);
      const next = [...current];
      next.splice(insertionIndex, 0, emptyParagraph(current));
      return next;
    });
    setActiveTab('matching');
  }

  function removeParagraph(blockId) {
    setBlocks((current) => current.filter((block) => block.block_id !== blockId));
  }

  function addRepresentation() {
    const custom = defaultCustomRepresentationSettings();
    setRepresentationSettings((current) => normalizeRepresentationSettings([
      ...current,
      {
        id: `custom-${Date.now()}`,
        name: nextRepresentationName(current),
        prompt: 'Write a concise representation of this paragraph.',
        background_color: custom.background_color,
        background_opacity: custom.background_opacity,
        enabled: true,
        isDefault: false,
      },
    ]));
    setActiveTab('representations');
  }

  function updateRepresentation(id, patch) {
    setRepresentationSettings((current) => normalizeRepresentationSettings(
      current.map((setting) => (setting.id === id ? { ...setting, ...patch } : setting)),
    ));
  }

  function removeRepresentation(id) {
    setRepresentationSettings((current) => {
      const next = current.filter((setting) => setting.id !== id);
      return next.length ? normalizeRepresentationSettings(next) : [];
    });
  }

  if (errorMessage && !previewDocument) {
    return (
      <main className="study-shell">
        <section className="study-card">
          <p className="landing-kicker">Study designer</p>
          <h1 className="landing-title">Document preparation</h1>
          <DesignerUploadForm
            isProcessing={isProcessing}
            llmModel={llmModel}
            onDocumentIdChange={setUploadDocumentId}
            onFileChange={setUploadFile}
            onModelChange={setLlmModel}
            onSubmit={processUploadedDocument}
            uploadDocumentId={uploadDocumentId}
            uploadFile={uploadFile}
          />
          <p className="error-banner">{errorMessage}</p>
        </section>
      </main>
    );
  }

  if (!previewDocument) {
    return (
      <main className="study-shell">
        <section className="study-card">
          <p className="landing-kicker">Study designer</p>
          <h1 className="landing-title">Prepare document</h1>
          <DesignerUploadForm
            isProcessing={isProcessing}
            llmModel={llmModel}
            onDocumentIdChange={setUploadDocumentId}
            onFileChange={setUploadFile}
            onModelChange={setLlmModel}
            onSubmit={processUploadedDocument}
            uploadDocumentId={uploadDocumentId}
            uploadFile={uploadFile}
          />
          {documents.length ? <p className="study-copy">Loading existing study document...</p> : null}
        </section>
      </main>
    );
  }

  const designerPanel = (
    <DesignerPanel
      activeTab={activeTab}
      blocks={blocks}
      chunks={flattenedChunks}
      documents={documents}
      errorMessage={errorMessage}
      generationProgress={generationProgress}
      isGenerating={isGenerating}
      isProcessing={isProcessing}
      llmApiKey={llmApiKey}
      llmModel={llmModel}
      onActiveTabChange={setActiveTab}
      onAddParagraph={addParagraph}
      onAddRepresentation={addRepresentation}
      onDocumentChange={handleDocumentChange}
      onApiKeyChange={setLlmApiKey}
      onDocumentIdChange={setUploadDocumentId}
      onFileChange={setUploadFile}
      onGenerateRepresentations={generateRepresentations}
      onInsertParagraph={insertParagraph}
      onModelChange={setLlmModel}
      onRemoveParagraph={removeParagraph}
      onRemoveRepresentation={removeRepresentation}
      onSaveDocument={saveDocument}
      onSaveRepresentations={saveRepresentations}
      onSubmitUpload={processUploadedDocument}
      onUpdateBlock={updateBlock}
      onUpdateRepresentation={updateRepresentation}
      representationSettings={representationSettings}
      selectedDocumentId={selectedDocumentId}
      sourceMode={sourceMode}
      statusMessage={statusMessage}
      uploadDocumentId={uploadDocumentId}
      uploadFile={uploadFile}
    />
  );

  return (
    <ReaderPage
      defaultShowDebugLabels
      document={previewDocument}
      forceDebugLabels
      headerKicker="Study designer"
      onClearRepresentationCookie={() => {}}
      onLoadRepresentationCookie={() => {}}
      onRegenerateRepresentations={() => {}}
      onRepresentationSettingsChange={setRepresentationSettings}
      onReset={() => handleDocumentChange(selectedDocumentId)}
      onResetRepresentationSettings={() => setRepresentationSettings(resetRepresentationSettings())}
      onSaveRepresentationCookie={() => {}}
      quizPanel={designerPanel}
      representationSettings={representationSettings}
      resetLabel="Reload document"
      settingsMessage=""
      showFloatingSettings={false}
    />
  );
}

function DesignerPanel({
  activeTab,
  blocks,
  chunks,
  documents,
  errorMessage,
  generationProgress,
  isGenerating,
  isProcessing,
  llmApiKey,
  llmModel,
  onActiveTabChange,
  onAddParagraph,
  onAddRepresentation,
  onApiKeyChange,
  onDocumentIdChange,
  onDocumentChange,
  onFileChange,
  onGenerateRepresentations,
  onInsertParagraph,
  onModelChange,
  onRemoveParagraph,
  onRemoveRepresentation,
  onSaveDocument,
  onSaveRepresentations,
  onSubmitUpload,
  onUpdateBlock,
  onUpdateRepresentation,
  representationSettings,
  selectedDocumentId,
  sourceMode,
  statusMessage,
  uploadDocumentId,
  uploadFile,
}) {
  return (
    <aside className="designer-panel">
      {statusMessage ? <p className="reader-settings-status">{statusMessage}</p> : null}
      {errorMessage ? <p className="error-banner">{errorMessage}</p> : null}

      <div className="designer-tabs">
        {DESIGNER_TABS.map((tab) => (
          <button
            className={`designer-tab${activeTab === tab.id ? ' designer-tab-active' : ''}`}
            key={tab.id}
            onClick={() => onActiveTabChange(tab.id)}
            type="button"
          >
            {tab.label}
          </button>
        ))}
      </div>

      {activeTab === 'upload' ? (
        <UploadPreprocessTab
          documents={documents}
          isProcessing={isProcessing}
          llmModel={llmModel}
          onDocumentChange={onDocumentChange}
          onDocumentIdChange={onDocumentIdChange}
          onFileChange={onFileChange}
          onModelChange={onModelChange}
          onSubmitUpload={onSubmitUpload}
          selectedDocumentId={selectedDocumentId}
          sourceMode={sourceMode}
          uploadDocumentId={uploadDocumentId}
          uploadFile={uploadFile}
        />
      ) : null}

      {activeTab === 'matching' ? (
        <ParagraphEditor
          blocks={blocks}
          chunks={chunks}
          onAddParagraph={onAddParagraph}
          onInsertParagraph={onInsertParagraph}
          onRemoveParagraph={onRemoveParagraph}
          onSaveDocument={onSaveDocument}
          onUpdateBlock={onUpdateBlock}
        />
      ) : null}

      {activeTab === 'representations' ? (
        <RepresentationEditor
          generationProgress={generationProgress}
          isGenerating={isGenerating}
          llmApiKey={llmApiKey}
          llmModel={llmModel}
          onAddRepresentation={onAddRepresentation}
          onApiKeyChange={onApiKeyChange}
          onGenerateRepresentations={onGenerateRepresentations}
          onModelChange={onModelChange}
          onRemoveRepresentation={onRemoveRepresentation}
          onSaveRepresentations={onSaveRepresentations}
          onUpdateRepresentation={onUpdateRepresentation}
          representationSettings={representationSettings}
        />
      ) : null}
    </aside>
  );
}

function DesignerUploadForm({
  compact = false,
  isProcessing,
  llmModel,
  onDocumentIdChange,
  onFileChange,
  onModelChange,
  onSubmit,
  uploadDocumentId,
}) {
  return (
    <form className={`designer-upload-form${compact ? ' designer-upload-compact' : ''}`} onSubmit={onSubmit}>
      <div>
        <strong>Process PDF</strong>
        <p className="designer-helper-text">
          Upload runs OpenDataLoader only. Paragraph matching and representation generation happen later.
        </p>
      </div>
      <label className="settings-field">
        <span>PDF</span>
        <input
          accept="application/pdf"
          className="text-input"
          onChange={(event) => onFileChange(event.target.files?.[0] ?? null)}
          type="file"
        />
      </label>
      <label className="settings-field">
        <span>Document id</span>
        <input
          className="text-input"
          onChange={(event) => onDocumentIdChange(event.target.value)}
          placeholder="Optional; defaults to filename"
          type="text"
          value={uploadDocumentId}
        />
      </label>
      <label className="settings-field">
        <span>Default representation model</span>
        <input
          className="text-input"
          onChange={(event) => onModelChange(event.target.value)}
          placeholder="Optional; used later for representations"
          type="text"
          value={llmModel}
        />
      </label>
      <button className="primary-button" disabled={isProcessing} type="submit">
        {isProcessing ? 'Processing...' : 'Process document'}
      </button>
    </form>
  );
}

function UploadPreprocessTab({
  documents,
  isProcessing,
  llmModel,
  onDocumentChange,
  onDocumentIdChange,
  onFileChange,
  onModelChange,
  onSubmitUpload,
  selectedDocumentId,
  sourceMode,
  uploadDocumentId,
  uploadFile,
}) {
  return (
    <section className="designer-editor-section">
      <div className="designer-panel-header">
        <label className="settings-field">
          <span>Current document</span>
          <select
            className="text-input"
            onChange={(event) => onDocumentChange(event.target.value)}
            value={selectedDocumentId}
          >
            {documents.map((document) => (
              <option key={document.document_id} value={document.document_id}>
                {document.title || document.document_id}
              </option>
            ))}
          </select>
        </label>
        <span className="designer-source-chip">{sourceMode}</span>
      </div>
      <DesignerUploadForm
        isProcessing={isProcessing}
        llmModel={llmModel}
        onDocumentIdChange={onDocumentIdChange}
        onFileChange={onFileChange}
        onModelChange={onModelChange}
        onSubmit={onSubmitUpload}
        uploadDocumentId={uploadDocumentId}
        uploadFile={uploadFile}
      />
    </section>
  );
}

function ParagraphEditor({
  blocks,
  chunks,
  onAddParagraph,
  onInsertParagraph,
  onRemoveParagraph,
  onSaveDocument,
  onUpdateBlock,
}) {
  return (
    <section className="designer-editor-section">
      <div className="designer-section-heading">
        <strong>Paragraph matching</strong>
        <button className="settings-mini-button" onClick={onAddParagraph} type="button">
          Add
        </button>
      </div>
      <button className="primary-button" onClick={onSaveDocument} type="button">
        Save document
      </button>
      <div className="designer-card-list">
        {blocks.map((block, index) => (
          <section className="designer-card" key={block.block_id}>
            <div className="designer-card-row">
              <div>
                <div className="designer-chunk-id">{paragraphDisplayId(index)}</div>
                <p className="designer-helper-text">{selectedChunkSummary(block.chunk_ids ?? [], chunks)}</p>
              </div>
              <button
                className="settings-mini-button"
                onClick={() => onRemoveParagraph(block.block_id)}
                type="button"
              >
                Remove
              </button>
            </div>
            <div className="designer-actions">
              <button className="settings-mini-button" onClick={() => onInsertParagraph(block.block_id, 'above')} type="button">
                Add above
              </button>
              <button className="settings-mini-button" onClick={() => onInsertParagraph(block.block_id, 'below')} type="button">
                Add below
              </button>
            </div>
            <div className="designer-paragraph-preview">
              <strong>Text preview</strong>
              <p>{paragraphTextPreview(block, chunks)}</p>
            </div>
            <div className="designer-chunk-button-grid">
              {chunks.map((chunk) => {
                const selected = (block.chunk_ids ?? []).includes(chunk.chunk_id);
                return (
                  <button
                    className={`designer-chunk-button${selected ? ' designer-chunk-button-selected' : ''}`}
                    key={chunk.chunk_id}
                    onClick={() => toggleChunkForBlock({ block, chunkId: chunk.chunk_id, chunks, onUpdateBlock })}
                    type="button"
                  >
                    <span>{chunk.chunk_id}</span>
                    <small>p{chunk.page_number} | {truncate(chunk.text, 92)}</small>
                  </button>
                );
              })}
            </div>
          </section>
        ))}
      </div>
    </section>
  );
}

function RepresentationEditor({
  generationProgress,
  isGenerating,
  llmApiKey,
  llmModel,
  onAddRepresentation,
  onApiKeyChange,
  onGenerateRepresentations,
  onModelChange,
  onRemoveRepresentation,
  onSaveRepresentations,
  onUpdateRepresentation,
  representationSettings,
}) {
  return (
    <section className="designer-editor-section">
      <div className="designer-section-heading">
        <strong>Representation generation</strong>
        <button className="settings-mini-button" onClick={onAddRepresentation} type="button">
          Add
        </button>
      </div>
      <div className="designer-generation-box">
        <label className="settings-field">
          <span>OpenAI key</span>
          <input
            className="text-input"
            onChange={(event) => onApiKeyChange(event.target.value)}
            placeholder="Required unless backend has OPENAI_API_KEY"
            type="password"
            value={llmApiKey}
          />
        </label>
        <label className="settings-field">
          <span>Model</span>
          <input
            className="text-input"
            onChange={(event) => onModelChange(event.target.value)}
            placeholder="gpt-5-nano"
            type="text"
          value={llmModel}
        />
      </label>
      </div>
      <div className="designer-card-list">
        {!representationSettings.length ? (
          <p className="designer-helper-text">No prompts configured.</p>
        ) : null}
        {representationSettings.map((setting) => (
          <section className="designer-card" key={setting.id}>
            <div className="reader-representation-row">
              <label className="inline-control">
                <input
                  checked={setting.enabled}
                  onChange={(event) => onUpdateRepresentation(setting.id, { enabled: event.target.checked })}
                  type="checkbox"
                />
                <span>Visible</span>
              </label>
              <input
                aria-label={`${setting.name} background color`}
                className="settings-color-input"
                onChange={(event) => onUpdateRepresentation(setting.id, { background_color: event.target.value })}
                type="color"
                value={setting.background_color}
              />
              <label className="settings-opacity-control">
                <span>Opacity {Math.round((setting.background_opacity ?? 1) * 100)}%</span>
                <input
                  max="1"
                  min="0"
                  onChange={(event) => onUpdateRepresentation(setting.id, { background_opacity: Number(event.target.value) })}
                  step="0.05"
                  type="range"
                  value={setting.background_opacity ?? 1}
                />
              </label>
              <button
                className="settings-mini-button"
                onClick={() => onRemoveRepresentation(setting.id)}
                type="button"
              >
                Remove
              </button>
            </div>
            <label className="settings-field">
              <span>Name</span>
              <input
                className="text-input"
                onChange={(event) => onUpdateRepresentation(setting.id, { name: event.target.value })}
                type="text"
                value={setting.name}
              />
            </label>
            <label className="settings-field">
              <span>Prompt</span>
              <textarea
                className="text-input designer-textarea"
                onChange={(event) => onUpdateRepresentation(setting.id, { prompt: event.target.value })}
                rows={5}
                value={setting.prompt}
              />
            </label>
          </section>
        ))}
      </div>
      <div className="designer-bottom-actions">
        {isGenerating || generationProgress ? (
          <GenerationProgressBar progress={generationProgress} />
        ) : null}
        <button className="primary-button" disabled={isGenerating} onClick={onGenerateRepresentations} type="button">
          {isGenerating ? 'Applying...' : 'Apply'}
        </button>
        <button className="secondary-button" onClick={onSaveRepresentations} type="button">
          Save representations
        </button>
      </div>
    </section>
  );
}

function GenerationProgressBar({ progress }) {
  const total = Number(progress?.total_jobs ?? 0);
  const completed = Number(progress?.completed_jobs ?? 0);
  const failed = Number(progress?.failed_jobs ?? 0);
  const done = completed + failed;
  const percent = total ? Math.round((done / total) * 100) : 0;
  const label = total
    ? `${done}/${total} complete${failed ? `, ${failed} failed` : ''}`
    : 'Preparing representation jobs...';

  return (
    <div className="designer-progress">
      <progress max={total || undefined} value={total ? done : undefined} />
      <div className="designer-progress-row">
        <span>{label}</span>
        {total ? <span>{percent}%</span> : null}
      </div>
    </div>
  );
}

function requestedDocumentId() {
  const match = window.location.pathname.match(/^\/prepare-document\/([^/]+)$/);
  if (match) {
    return decodeURIComponent(match[1]);
  }
  return new URLSearchParams(window.location.search).get('document') || '';
}

async function fetchJson(url) {
  const response = await fetch(url);
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.detail || `Unable to load ${url}.`);
  }
  return payload;
}

function normalizePages(pages) {
  return safeArray(pages).map((page, pageIndex) => ({
    ...page,
    page_number: Number(page.page_number) || pageIndex + 1,
    chunks: safeArray(page.chunks).map((chunk) => ({ ...chunk })),
  }));
}

function mergeParagraphBlocks(documentBlocks, paragraphBlocks) {
  const blocksById = new Map(safeArray(documentBlocks).map((block) => [block.block_id, block]));
  const sourceBlocks = safeArray(paragraphBlocks).length ? paragraphBlocks : documentBlocks;
  return safeArray(sourceBlocks)
    .filter((block) => block && typeof block === 'object')
    .map((block, index) => ({
      ...(blocksById.get(block.block_id) ?? {}),
      ...block,
      block_id: String(block.block_id || `paragraph-${index + 1}`),
      chunk_ids: safeArray(block.chunk_ids).map(String),
      page_number: Number(block.page_number) || 1,
      representations: safeArray(block.representations),
      section_path: safeArray(block.section_path).map(String),
      text: String(block.text ?? ''),
    }));
}

function settingsFromDefinitions(definitions) {
  if (!Array.isArray(definitions) || !definitions.length) {
    return resetRepresentationSettings();
  }
  return normalizeRepresentationSettings(definitions.map((definition, index) => {
    const name = String(definition.name || `representation-${index + 1}`);
    return {
      ...definition,
      id: cssSafeId(name) || `representation-${index + 1}`,
      isDefault: name === 'keywords' || name === 'summary',
    };
  }));
}

function configFromDocument(document) {
  return {
    llm: {
      enabled: document.metadata?.llm_representations?.enabled ?? true,
      model: document.metadata?.llm_representations?.model ?? '',
      representations: document.metadata?.representation_definitions ?? [],
    },
    provider: document.provider ?? document.metadata?.provider ?? 'opendataloader',
    source_name: document.source_name ?? '',
  };
}

function flattenChunks(pages) {
  return safeArray(pages).flatMap((page) => safeArray(page.chunks).map((chunk) => ({
    ...chunk,
    page_number: chunk.page_number ?? page.page_number,
  })));
}

function buildDocumentPayload({
  baseDocument,
  blocks,
  designerMode = false,
  documentId,
  pages,
  pdfUrl,
  renumberParagraphs = false,
  representationSettings,
}) {
  const flatChunks = flattenChunks(pages);
  const chunkPageById = new Map(flatChunks.map((chunk) => [chunk.chunk_id, chunk.page_number]));
  const normalizedBlocks = blocks.map((block, index) => {
    const chunkIds = safeArray(block.chunk_ids).map(String).filter(Boolean);
    const blockId = renumberParagraphs
      ? paragraphDisplayId(index)
      : String(block.block_id || paragraphDisplayId(index));
    return {
      ...block,
      block_id: blockId,
      chunk_ids: chunkIds,
      page_number: Number(block.page_number) || chunkPageById.get(chunkIds[0]) || 1,
      representations: safeArray(block.representations),
      section_path: safeArray(block.section_path).map(String),
      text: textForChunks(chunkIds, flatChunks) || String(block.text ?? ''),
    };
  });
  const blockIdsByChunkId = new Map();
  for (const block of normalizedBlocks) {
    for (const chunkId of block.chunk_ids) {
      const next = blockIdsByChunkId.get(chunkId) ?? [];
      next.push(block.block_id);
      blockIdsByChunkId.set(chunkId, next);
    }
  }
  const normalizedPages = safeArray(pages).map((page) => ({
    ...page,
    chunks: safeArray(page.chunks).map((chunk) => ({
      ...chunk,
      block_ids: blockIdsByChunkId.get(chunk.chunk_id) ?? [],
    })),
  }));
  const metadata = baseDocument.metadata && typeof baseDocument.metadata === 'object'
    ? { ...baseDocument.metadata }
    : {};
  metadata.paragraph_count = normalizedBlocks.length;
  metadata.representation_definitions = toRepresentationDefinitions(representationSettings);
  if (designerMode) {
    metadata.designer_mode = true;
  } else {
    delete metadata.designer_mode;
  }

  return {
    ...baseDocument,
    blocks: normalizedBlocks,
    document_id: baseDocument.document_id || documentId,
    metadata,
    page_count: normalizedPages.length,
    pages: normalizedPages,
    pdf_url: pdfUrl,
    study_document_id: documentId,
  };
}

function buildChunksPayload(pages) {
  return { pages };
}

function buildParagraphsPayload(blocks) {
  return {
    blocks: blocks.map((block) => ({
      block_id: block.block_id,
      chunk_ids: block.chunk_ids ?? [],
      page_number: block.page_number,
      representations: block.representations ?? [],
      section_path: block.section_path ?? [],
      text: block.text ?? '',
    })),
  };
}

function buildPromptPayload(documentId, representationSettings) {
  return {
    document_id: documentId,
    representations: toRepresentationDefinitions(representationSettings),
  };
}

function buildRepresentationsPayload(documentId, document) {
  return {
    document_id: documentId,
    blocks: safeArray(document?.blocks).map((block) => ({
      block_id: block.block_id,
      chunk_ids: block.chunk_ids ?? [],
      page_number: block.page_number,
      representations: block.representations ?? [],
      text: block.text ?? '',
    })),
    updated_at: new Date().toISOString(),
  };
}

function designerPdfUrl(documentId) {
  return `${BACKEND_DOCUMENTS_URL}/${encodeURIComponent(documentId)}/file`;
}

function emptyParagraph(blocks) {
  return {
    block_id: nextDraftParagraphId(blocks),
    chunk_ids: [],
    page_number: 1,
    representations: [],
    section_path: [],
    text: '',
  };
}

function paragraphDisplayId(index) {
  return `paragraph-${index + 1}`;
}

function toggleChunkForBlock({ block, chunkId, chunks, onUpdateBlock }) {
  const selected = new Set(safeArray(block.chunk_ids).map(String));
  if (selected.has(chunkId)) {
    selected.delete(chunkId);
  } else {
    selected.add(chunkId);
  }
  const nextChunkIds = chunks
    .map((chunk) => chunk.chunk_id)
    .filter((id) => selected.has(id));
  onUpdateBlock(block.block_id, {
    chunk_ids: nextChunkIds,
    page_number: firstSelectedPageNumber(nextChunkIds, chunks, block.page_number),
    text: textForChunks(nextChunkIds, chunks),
  });
}

function selectedChunkSummary(chunkIds, chunks) {
  const ids = safeArray(chunkIds).map(String);
  if (!ids.length) {
    return 'No chunks selected.';
  }
  const selectedChunks = chunks.filter((chunk) => ids.includes(chunk.chunk_id));
  const pageNumbers = [...new Set(selectedChunks.map((chunk) => chunk.page_number).filter(Boolean))];
  const preview = ids.slice(0, 4).join(', ');
  const suffix = ids.length > 4 ? `, +${ids.length - 4} more` : '';
  const pages = pageNumbers.length ? ` on p${pageNumbers.join(', p')}` : '';
  return `${ids.length} selected${pages}: ${preview}${suffix}`;
}

function paragraphTextPreview(block, chunks) {
  const text = textForChunks(block.chunk_ids ?? [], chunks) || block.text || '';
  return text ? truncate(text, 700) : 'Select one or more chunks to preview this paragraph.';
}

function firstSelectedPageNumber(chunkIds, chunks, fallback) {
  const firstChunk = chunks.find((chunk) => chunkIds.includes(chunk.chunk_id));
  return firstChunk?.page_number ?? fallback ?? 1;
}

function textForChunks(chunkIds, chunks) {
  const selected = chunks.filter((chunk) => chunkIds.includes(chunk.chunk_id));
  return selected.map((chunk) => chunk.text).filter(Boolean).join('\n\n');
}

function nextDraftParagraphId(blocks) {
  const existing = new Set(blocks.map((block) => block.block_id));
  let index = blocks.length + 1;
  let candidate = `draft-paragraph-${index}`;
  while (existing.has(candidate)) {
    index += 1;
    candidate = `draft-paragraph-${index}`;
  }
  return candidate;
}

function nextRepresentationName(settings) {
  const existing = new Set(settings.map((setting) => setting.name.toLowerCase()));
  let index = 1;
  while (existing.has(`custom ${index}`)) {
    index += 1;
  }
  return `custom ${index}`;
}

function upsertDocument(documents, nextDocument) {
  const withoutExisting = documents.filter((document) => document.document_id !== nextDocument.document_id);
  return [...withoutExisting, nextDocument].sort((first, second) => (
    first.document_id.localeCompare(second.document_id)
  ));
}

function downloadJson(filename, payload) {
  const blob = new Blob([`${JSON.stringify(payload, null, 2)}\n`], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function formatSavedPaths(savedPaths) {
  const entries = Object.entries(savedPaths ?? {});
  if (!entries.length) {
    return 'No file paths were returned by the backend.';
  }
  return entries.map(([name, path]) => `${name}: ${path}`).join('\n');
}

function truncate(value, maxLength) {
  const text = String(value ?? '').replace(/\s+/g, ' ').trim();
  return text.length > maxLength ? `${text.slice(0, maxLength - 1)}...` : text;
}

function cssSafeId(value) {
  return String(value || '').toLowerCase().replace(/[^a-z0-9_-]+/g, '-').replace(/^-+|-+$/g, '');
}

function safeArray(value) {
  return Array.isArray(value) ? value : [];
}
