import { useEffect, useMemo, useState } from 'react';
import LandingPage from './components/LandingPage';
import ReaderPage from './components/ReaderPage';

function getRouteDocumentId() {
  const match = window.location.pathname.match(/^\/reader\/([^/]+)$/);
  return match ? match[1] : null;
}

export default function App() {
  const [documentData, setDocumentData] = useState(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState('');
  const routeDocumentId = useMemo(getRouteDocumentId, [window.location.pathname]);

  useEffect(() => {
    if (!routeDocumentId || documentData?.document_id === routeDocumentId) {
      return;
    }

    setErrorMessage('This route has no loaded document in the current page state. Please import the PDF again.');
  }, [documentData, routeDocumentId]);

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
    await submitDocument(`/api/documents/upload?provider=${encodeURIComponent(provider)}`, { body, method: 'POST' });
  }

  async function handleUrlSubmit(url, provider, representationOptions) {
    await submitDocument('/api/documents/from-url', {
      body: JSON.stringify({
        provider,
        url,
        llm_options: buildLlmOptionsPayload(representationOptions),
      }),
      headers: { 'Content-Type': 'application/json' },
      method: 'POST',
    });
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
    }} />
  ) : (
    <LandingPage
      errorMessage={errorMessage}
      isSubmitting={isSubmitting}
      onUploadSubmit={handleUploadSubmit}
      onUrlSubmit={handleUrlSubmit}
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
    keyword_min_words: toPositiveNumber(representationOptions.keywordMinWords, 4),
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
    && (first.text ?? null) === (second.text ?? null)
    && arraysEqual(first.items ?? [], second.items ?? [])
  );
}

function objectsEqual(first, second) {
  return JSON.stringify(first ?? {}) === JSON.stringify(second ?? {});
}

function arraysEqual(first, second) {
  if (first.length !== second.length) {
    return false;
  }
  return first.every((item, index) => item === second[index]);
}
