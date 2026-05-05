import { useEffect, useMemo, useRef, useState } from 'react';
import LandingPage from './components/LandingPage';
import ReaderPage from './components/ReaderPage';
import {
  clearCookieRepresentationSettings,
  readCookieRepresentationSettings,
  readSessionRepresentationSettings,
  resetRepresentationSettings,
  saveCookieRepresentationSettings,
  toRepresentationDefinitions,
  writeSessionRepresentationSettings,
} from './representationSettings';

function getRouteDocumentId() {
  const match = window.location.pathname.match(/^\/reader\/([^/]+)$/);
  return match ? match[1] : null;
}

export default function App() {
  const [documentData, setDocumentData] = useState(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState('');
  const [representationSettings, setRepresentationSettings] = useState(readSessionRepresentationSettings);
  const [settingsMessage, setSettingsMessage] = useState('');
  const routeDocumentId = useMemo(getRouteDocumentId, [window.location.pathname]);
  const reportedRepresentationFailuresRef = useRef(new Set());

  useEffect(() => {
    if (!routeDocumentId || documentData?.document_id === routeDocumentId) {
      return;
    }

    setErrorMessage('This route has no loaded document in the current page state. Please import the PDF again.');
  }, [documentData, routeDocumentId]);

  useEffect(() => {
    reportedRepresentationFailuresRef.current.clear();
  }, [documentData?.document_id]);

  useEffect(() => {
    if (!documentData?.metadata?.llm_representations) {
      return;
    }
    reportRepresentationFailuresToConsole({
      documentId: documentData.document_id,
      provider: documentData.provider ?? documentData.metadata?.provider ?? 'opendataloader',
      reportedFailures: reportedRepresentationFailuresRef.current,
      status: documentData.metadata.llm_representations,
    });
  }, [
    documentData?.document_id,
    documentData?.provider,
    documentData?.metadata?.llm_representations,
  ]);

  useEffect(() => {
    const representationStatus = documentData?.metadata?.llm_representations;
    if (
      !documentData?.document_id
      || !representationStatus?.enabled
      || representationStatus.status === 'complete'
      || representationStatus.status === 'failed'
    ) {
      return undefined;
    }

    let cancelled = false;
    const provider = documentData.provider ?? documentData.metadata?.provider ?? 'opendataloader';

    async function pollRepresentations() {
      try {
        const endpoint = `/api/documents/${documentData.document_id}/representations?provider=${encodeURIComponent(provider)}`;
        const response = await fetch(endpoint);
        const payload = await response.json();
        if (!response.ok || cancelled) {
          return;
      }
      if (payload.status !== 'failed') {
        clearRepresentationErrorMessage(setErrorMessage);
      }
      reportRepresentationFailuresToConsole({
        documentId: documentData.document_id,
        provider,
        reportedFailures: reportedRepresentationFailuresRef.current,
        status: payload,
      });
      setDocumentData((current) => mergeRepresentationSnapshot(current, payload));
    } catch {
      // Polling is best-effort; the reader remains usable without generated badges.
      }
    }

    pollRepresentations();
    const intervalId = window.setInterval(pollRepresentations, 1600);
    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
    };
  }, [
    documentData?.document_id,
    documentData?.provider,
    documentData?.metadata?.llm_representations?.enabled,
    documentData?.metadata?.llm_representations?.status,
  ]);

  async function handleUploadSubmit(file, provider, representationOptions) {
    const body = new FormData();
    body.append('file', file);
    appendRepresentationFormFields(body, representationOptions);
    body.append('representations', JSON.stringify(toRepresentationDefinitions(representationSettings)));
    await submitDocument(`/api/documents/upload?provider=${encodeURIComponent(provider)}`, { body, method: 'POST' });
  }

  async function handleUrlSubmit(url, provider, representationOptions) {
    await submitDocument('/api/documents/from-url', {
      body: JSON.stringify({
        provider,
        url,
        llm_options: {
          ...buildLlmOptionsPayload(representationOptions),
          representations: toRepresentationDefinitions(representationSettings),
        },
      }),
      headers: { 'Content-Type': 'application/json' },
      method: 'POST',
    });
  }

  function handleRepresentationSettingsChange(nextSettings) {
    setSettingsMessage('');
    setRepresentationSettings(nextSettings);
    writeSessionRepresentationSettings(nextSettings);
  }

  function handleResetRepresentationSettings() {
    const nextSettings = resetRepresentationSettings();
    handleRepresentationSettingsChange(nextSettings);
    setSettingsMessage('Representation settings reset for this session.');
  }

  function handleSaveRepresentationCookie() {
    saveCookieRepresentationSettings(representationSettings);
    setSettingsMessage('Representation settings saved to cookies.');
  }

  function handleLoadRepresentationCookie() {
    const cookieSettings = readCookieRepresentationSettings();
    if (!cookieSettings) {
      setSettingsMessage('No saved cookie settings found.');
      return;
    }
    handleRepresentationSettingsChange(cookieSettings);
    setSettingsMessage('Cookie settings loaded into this session.');
  }

  function handleClearRepresentationCookie() {
    clearCookieRepresentationSettings();
    setSettingsMessage('Saved cookie settings cleared.');
  }

  async function handleRegenerateRepresentations(options = {}) {
    if (!documentData?.document_id) {
      return;
    }

    setErrorMessage('');
    setSettingsMessage('');
    try {
      const provider = documentData.provider ?? documentData.metadata?.provider ?? 'opendataloader';
      const response = await fetch(`/api/documents/${documentData.document_id}/representations/regenerate`, {
        body: JSON.stringify({
          provider,
          llm_options: {
            enabled: true,
            api_key: options.apiKey?.trim() || null,
            model: options.model?.trim() || documentData.metadata?.llm_representations?.model || null,
            representations: toRepresentationDefinitions(representationSettings),
          },
        }),
        headers: { 'Content-Type': 'application/json' },
        method: 'POST',
      });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.detail ?? 'Unable to regenerate representations.');
      }
      setDocumentData(payload);
      setSettingsMessage('Representation jobs restarted for this document.');
    } catch (error) {
      setErrorMessage(error.message);
      setSettingsMessage(error.message);
    }
  }

  async function submitDocument(endpoint, requestInit) {
    setIsSubmitting(true);
    setErrorMessage('');

    try {
      const response = await fetch(endpoint, requestInit);
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.detail ?? 'Unable to import PDF.');
      }

      setDocumentData(payload);
      window.history.pushState({}, '', `/reader/${payload.document_id}`);
    } catch (error) {
      setErrorMessage(error.message);
    } finally {
      setIsSubmitting(false);
    }
  }

  useEffect(() => {
    const handlePopState = () => {
      if (getRouteDocumentId()) {
        return;
      }
      setDocumentData(null);
      setErrorMessage('');
    };

    window.addEventListener('popstate', handlePopState);
    return () => window.removeEventListener('popstate', handlePopState);
  }, []);

  return documentData ? (
    <ReaderPage document={documentData} onReset={() => {
      setDocumentData(null);
      setErrorMessage('');
      window.history.pushState({}, '', '/');
    }}
      onClearRepresentationCookie={handleClearRepresentationCookie}
      onLoadRepresentationCookie={handleLoadRepresentationCookie}
      onRegenerateRepresentations={handleRegenerateRepresentations}
      onRepresentationSettingsChange={handleRepresentationSettingsChange}
      onResetRepresentationSettings={handleResetRepresentationSettings}
      onSaveRepresentationCookie={handleSaveRepresentationCookie}
      representationSettings={representationSettings}
      settingsMessage={settingsMessage}
    />
  ) : (
    <LandingPage
      errorMessage={errorMessage}
      isSubmitting={isSubmitting}
      onUploadSubmit={handleUploadSubmit}
      onUrlSubmit={handleUrlSubmit}
      representationSettings={representationSettings}
    />
  );
}

function appendRepresentationFormFields(body, representationOptions) {
  const options = buildLlmOptionsPayload(representationOptions);
  body.append('llm_enabled', String(options.enabled));
  body.append('keyword_min_words', String(options.keyword_min_words));
  body.append('summary_min_words', String(options.summary_min_words));
  body.append('summary_word_ratio', String(options.summary_word_ratio));
  body.append('max_keywords', String(options.max_keywords));
  if (options.model) {
    body.append('llm_model', options.model);
  }
  if (options.api_key) {
    body.append('llm_api_key', options.api_key);
  }
}

function buildLlmOptionsPayload(representationOptions = {}) {
  const enabled = Boolean(representationOptions.llmEnabled);
  return {
    enabled,
    api_key: enabled ? representationOptions.apiKey?.trim() || null : null,
    model: representationOptions.model?.trim() || null,
    keyword_min_words: Math.max(toPositiveNumber(representationOptions.keywordMinWords, 20), 20),
    summary_min_words: toPositiveNumber(representationOptions.summaryMinWords, 35),
    summary_word_ratio: toPositiveNumber(representationOptions.summaryWordRatio, 0.15),
    max_keywords: toPositiveNumber(representationOptions.maxKeywords, 5),
  };
}

function toPositiveNumber(value, fallback) {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function mergeRepresentationSnapshot(current, snapshot) {
  if (!current) {
    return current;
  }

  const representationsByBlockId = new Map();
  for (const item of snapshot.representations ?? []) {
    const blockId = item.block_id;
    if (!blockId || !item.representation) {
      continue;
    }
    const existing = representationsByBlockId.get(blockId) ?? [];
    existing.push(item.representation);
    representationsByBlockId.set(blockId, existing);
  }

  let hasBlockChanges = false;
  const blocks = current.blocks.map((block) => {
    const generated = representationsByBlockId.get(block.block_id);
    if (!generated?.length) {
      return block;
    }
    const mergedByKind = new Map((block.representations ?? []).map((representation) => [representation.kind, representation]));
    for (const representation of generated) {
      const existing = mergedByKind.get(representation.kind);
      if (!representationsEqual(existing, representation)) {
        hasBlockChanges = true;
      }
      mergedByKind.set(representation.kind, representation);
    }
    if (!hasBlockChanges) {
      return block;
    }
    return { ...block, representations: [...mergedByKind.values()] };
  });

  const nextRepresentationStatus = {
    enabled: snapshot.enabled,
    model: snapshot.model,
    key_source: snapshot.key_source,
    status: snapshot.status,
    total_jobs: snapshot.total_jobs,
    completed_jobs: snapshot.completed_jobs,
    failed_jobs: snapshot.failed_jobs,
    running_jobs: snapshot.running_jobs,
    pending_jobs: snapshot.pending_jobs,
    errors: snapshot.errors ?? [],
  };
  const currentRepresentationStatus = current.metadata?.llm_representations ?? {};
  const hasStatusChanges = !objectsEqual(currentRepresentationStatus, nextRepresentationStatus);
  if (!hasBlockChanges && !hasStatusChanges) {
    return current;
  }

  return {
    ...current,
    blocks,
    metadata: {
      ...current.metadata,
      llm_representations: nextRepresentationStatus,
    },
  };
}

function representationsEqual(first, second) {
  if (!first || !second) {
    return first === second;
  }
  return (
    first.kind === second.kind
    && first.label === second.label
    && (first.value ?? '') === (second.value ?? '')
    && (first.background_color ?? '') === (second.background_color ?? '')
    && Number(first.background_opacity ?? 1) === Number(second.background_opacity ?? 1)
    && (first.text ?? null) === (second.text ?? null)
    && arraysEqual(first.items ?? [], second.items ?? [])
  );
}

function objectsEqual(first, second) {
  return JSON.stringify(first ?? {}) === JSON.stringify(second ?? {});
}

function clearRepresentationErrorMessage(setErrorMessage) {
  setErrorMessage((current) => (
    isRepresentationGenerationError(current) ? '' : current
  ));
}

function reportRepresentationFailuresToConsole({ documentId, provider, reportedFailures, status }) {
  if (!status?.enabled || toCount(status.failed_jobs) <= 0) {
    return;
  }

  const errors = Array.isArray(status.errors) ? status.errors : [];
  const signature = JSON.stringify({
    documentId,
    provider,
    failed_jobs: status.failed_jobs,
    errors: errors.map((item) => ({
      block_id: item?.block_id,
      kind: item?.kind,
      error: item?.error,
    })),
  });
  if (reportedFailures.has(signature)) {
    return;
  }

  reportedFailures.add(signature);
  console.error('LLM representation generation failed.', {
    document_id: documentId,
    provider,
    status: status.status,
    failed_jobs: toCount(status.failed_jobs),
    total_jobs: toCount(status.total_jobs),
    errors,
  });
}

function isRepresentationGenerationError(message) {
  return String(message || '').toLowerCase().includes('representation generation failed');
}

function toCount(value) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function arraysEqual(first, second) {
  if (first.length !== second.length) {
    return false;
  }
  return first.every((item, index) => item === second[index]);
}
