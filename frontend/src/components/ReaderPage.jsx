import { useState } from 'react';
import PdfReaderCanvas from './PdfReaderCanvas';

export default function ReaderPage({ document, onReset }) {
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const [visibleRepresentations, setVisibleRepresentations] = useState({
    keywords: true,
    summary: true,
  });

  function toggleRepresentation(kind) {
    setVisibleRepresentations((current) => ({ ...current, [kind]: !current[kind] }));
  }

  return (
    <main className="reader-shell">
      <header className="reader-header">
        <div>
          <p className="reader-kicker">Reader</p>
          <h1 className="reader-title">{document.title}</h1>
        </div>

        <button className="secondary-button" onClick={onReset} type="button">
          Import another PDF
        </button>
      </header>

      <PdfReaderCanvas document={document} visibleRepresentations={visibleRepresentations} />

      <div className="reader-settings">
        <RepresentationStatusIcon status={document.metadata?.llm_representations} />
        {isSettingsOpen ? (
          <div className="reader-settings-panel">
            <label className="inline-control">
              <input
                checked={visibleRepresentations.keywords}
                onChange={() => toggleRepresentation('keywords')}
                type="checkbox"
              />
              <span>Keywords</span>
            </label>
            <label className="inline-control">
              <input
                checked={visibleRepresentations.summary}
                onChange={() => toggleRepresentation('summary')}
                type="checkbox"
              />
              <span>Summaries</span>
            </label>
          </div>
        ) : null}
        <button
          aria-label="Representation settings"
          className="settings-button"
          onClick={() => setIsSettingsOpen((current) => !current)}
          type="button"
        >
          Settings
        </button>
      </div>
    </main>
  );
}

function RepresentationStatusIcon({ status }) {
  if (!status?.enabled) {
    return null;
  }

  const details = getRepresentationStatusDetails(status);
  return (
    <div
      aria-label={details.title}
      className={`llm-status-chip llm-status-${details.variant}`}
      role="status"
      title={details.title}
    >
      <span className="llm-status-dot" />
      <span className="llm-status-count">{details.label}</span>
    </div>
  );
}

function getRepresentationStatusDetails(status) {
  const total = toCount(status.total_jobs);
  const completed = toCount(status.completed_jobs);
  const failed = toCount(status.failed_jobs);
  const running = toCount(status.running_jobs);
  const pending = status.pending_jobs == null
    ? Math.max(total - completed - failed - running, 0)
    : toCount(status.pending_jobs);
  const firstError = status.errors?.[0]?.error;

  if (total === 0) {
    return {
      label: 'No LLM jobs',
      title: 'No paragraph blocks passed the configured keyword or summary thresholds.',
      variant: 'idle',
    };
  }

  if (status.status === 'failed') {
    return {
      label: `Failed ${failed}/${total}`,
      title: firstError
        ? `LLM representations failed: ${firstError}`
        : `LLM representations failed for ${failed} of ${total} jobs.`,
      variant: 'failed',
    };
  }

  if (status.status === 'complete') {
    return {
      label: `Done ${completed}/${total}`,
      title: `LLM representations complete: ${completed} of ${total} jobs finished.`,
      variant: 'complete',
    };
  }

  return {
    label: running > 0 ? `LLM ${completed}/${total} · ${running} running` : `LLM ${completed}/${total}`,
    title: `Generating LLM representations: ${completed} complete, ${running} running, ${pending} pending, ${failed} failed.`,
    variant: 'pending',
  };
}

function toCount(value) {
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? number : 0;
}
