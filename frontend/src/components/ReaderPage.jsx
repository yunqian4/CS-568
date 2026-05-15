import { useState } from 'react';
import PdfReaderCanvas from './PdfReaderCanvas';
import { defaultCustomRepresentationSettings } from '../representationSettings';

function nextCustomName(settings) {
  const names = new Set(settings.map((setting) => setting.name.trim().toLowerCase()));
  let index = 1;
  while (names.has(`custom ${index}`)) {
    index += 1;
  }
  return `custom ${index}`;
}

export default function ReaderPage({
  defaultShowDebugLabels = false,
  document,
  forceDebugLabels = false,
  headerKicker = 'Reader',
  onClearRepresentationCookie,
  onLoadRepresentationCookie,
  onRegenerateRepresentations,
  onRepresentationSettingsChange,
  onReset,
  onResetRepresentationSettings,
  onSaveRepresentationCookie,
  quizMode = false,
  quizPanel = null,
  representationSettings,
  resetLabel = 'Import another PDF',
  settingsMessage,
  showFloatingSettings = true,
}) {
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const [showDebugLabels, setShowDebugLabels] = useState(defaultShowDebugLabels);
  const [regenerationApiKey, setRegenerationApiKey] = useState('');
  const [regenerationModel, setRegenerationModel] = useState(document.metadata?.llm_representations?.model ?? '');
  const debugLabelsVisible = forceDebugLabels || showDebugLabels;
  const visibleRepresentations = Object.fromEntries(
    [
      ...representationSettings.flatMap((setting) => [[setting.id, setting.enabled], [setting.name, setting.enabled]]),
      ['block-label', debugLabelsVisible],
    ],
  );

  function updateSetting(id, patch) {
    onRepresentationSettingsChange(
      representationSettings.map((setting) => (setting.id === id ? { ...setting, ...patch } : setting)),
    );
  }

  function addRepresentation() {
    const customDefaults = defaultCustomRepresentationSettings();
    onRepresentationSettingsChange([
      ...representationSettings,
      {
        id: `custom-${Date.now()}`,
        name: nextCustomName(representationSettings),
        prompt: 'Write a concise representation of this paragraph.',
        background_color: customDefaults.background_color,
        background_opacity: customDefaults.background_opacity,
        enabled: true,
        isDefault: false,
      },
    ]);
  }

  function removeRepresentation(id) {
    onRepresentationSettingsChange(representationSettings.filter((setting) => setting.id !== id || setting.isDefault));
  }

  return (
    <main className="reader-shell">
      <header className="reader-header">
        <div>
          <p className="reader-kicker">{headerKicker}</p>
          <h1 className="reader-title">{document.title}</h1>
        </div>

        <button className="secondary-button" onClick={onReset} type="button">
          {resetLabel}
        </button>
      </header>

      <RepresentationFailureBanner status={document.metadata?.llm_representations} />

      <div className="reader-body">
        <div className="reader-pdf-wrapper">
          <PdfReaderCanvas
            document={document}
            representationSettings={representationSettings}
            visibleRepresentations={visibleRepresentations}
          />
        </div>
        {quizPanel}
      </div>

      {quizMode || !showFloatingSettings ? null : <div className="reader-settings">
        <RepresentationStatusIcon status={document.metadata?.llm_representations} />
        {isSettingsOpen ? (
          <div className="reader-settings-panel">
            <div className="reader-settings-heading">
              <strong>Representations</strong>
              <button className="settings-mini-button" onClick={addRepresentation} type="button">Add</button>
            </div>
            <label className="inline-control reader-debug-toggle">
              <input
                checked={debugLabelsVisible}
                disabled={forceDebugLabels}
                onChange={(event) => setShowDebugLabels(event.target.checked)}
                type="checkbox"
              />
              <span>Paragraph IDs</span>
            </label>
            <div className="reader-representation-list">
              {representationSettings.map((setting) => (
                <section className="reader-representation-card" key={setting.id}>
                  <div className="reader-representation-row">
                    <label className="inline-control">
                      <input
                        checked={setting.enabled}
                        onChange={(event) => updateSetting(setting.id, { enabled: event.target.checked })}
                        type="checkbox"
                      />
                      <span>Visible</span>
                    </label>
                    <input
                      aria-label={`${setting.name} background color`}
                      className="settings-color-input"
                      onChange={(event) => updateSetting(setting.id, { background_color: event.target.value })}
                      type="color"
                      value={setting.background_color}
                    />
                    <label className="settings-opacity-control">
                      <span>Opacity {Math.round((setting.background_opacity ?? 1) * 100)}%</span>
                      <input
                        aria-label={`${setting.name} background opacity`}
                        max="1"
                        min="0"
                        onChange={(event) => updateSetting(setting.id, { background_opacity: Number(event.target.value) })}
                        step="0.05"
                        type="range"
                        value={setting.background_opacity ?? 1}
                      />
                    </label>
                    {setting.isDefault ? null : (
                      <button
                        className="settings-mini-button"
                        onClick={() => removeRepresentation(setting.id)}
                        type="button"
                      >
                        Remove
                      </button>
                    )}
                  </div>
                  <label className="settings-field">
                    <span>Name</span>
                    <input
                      className="text-input"
                      onChange={(event) => updateSetting(setting.id, { name: event.target.value })}
                      type="text"
                      value={setting.name}
                    />
                  </label>
                  <label className="settings-field">
                    <span>Prompt</span>
                    <textarea
                      className="text-input settings-prompt-input"
                      onChange={(event) => updateSetting(setting.id, { prompt: event.target.value })}
                      rows={4}
                      value={setting.prompt}
                    />
                  </label>
                </section>
              ))}
            </div>
            <div className="reader-settings-actions">
              <input
                className="text-input settings-regenerate-input"
                onChange={(event) => setRegenerationApiKey(event.target.value)}
                placeholder="OpenAI key for regeneration"
                type="password"
                value={regenerationApiKey}
              />
              <input
                className="text-input settings-regenerate-input"
                onChange={(event) => setRegenerationModel(event.target.value)}
                placeholder="Model"
                type="text"
                value={regenerationModel}
              />
              <button
                className="primary-button settings-panel-button"
                onClick={() => onRegenerateRepresentations({ apiKey: regenerationApiKey, model: regenerationModel })}
                type="button"
              >
                Submit prompts
              </button>
              <button className="secondary-button settings-panel-button" onClick={onResetRepresentationSettings} type="button">
                Reset defaults
              </button>
              <button className="secondary-button settings-panel-button" onClick={onSaveRepresentationCookie} type="button">
                Save cookie
              </button>
              <button className="secondary-button settings-panel-button" onClick={onLoadRepresentationCookie} type="button">
                Load cookie
              </button>
              <button className="secondary-button settings-panel-button" onClick={onClearRepresentationCookie} type="button">
                Clear cookie
              </button>
            </div>
            {settingsMessage ? <p className="reader-settings-status">{settingsMessage}</p> : null}
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
      </div>}
    </main>
  );
}

function RepresentationFailureBanner({ status }) {
  if (!status?.enabled || toCount(status.failed_jobs) <= 0) {
    return null;
  }

  const firstError = status.errors?.[0];
  const message = firstError?.error
    ? `LLM representation failed for ${firstError.block_id ?? 'a block'} ${firstError.kind ? `(${firstError.kind})` : ''}: ${firstError.error}`
    : `LLM representations failed for ${toCount(status.failed_jobs)} of ${toCount(status.total_jobs)} jobs.`;

  return <p className="error-banner reader-error-banner">{message}</p>;
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
