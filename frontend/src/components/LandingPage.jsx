import { useEffect, useState } from 'react';

export default function LandingPage({ errorMessage, isSubmitting, onUploadSubmit, onUrlSubmit }) {
  const [selectedFile, setSelectedFile] = useState(null);
  const [pdfUrl, setPdfUrl] = useState('');
  const [provider, setProvider] = useState('opendataloader');
  const [llmEnabled, setLlmEnabled] = useState(true);
  const [quizMode, setQuizMode] = useState(false);
  const [quizRepresentationChoice, setQuizRepresentationChoice] = useState('random');
  const [apiKey, setApiKey] = useState('');
  const [model, setModel] = useState('');
  const [keywordMinWords, setKeywordMinWords] = useState('20');
  const [summaryMinWords, setSummaryMinWords] = useState('35');
  const [summaryWordRatio, setSummaryWordRatio] = useState('0.15');
  const [maxKeywords, setMaxKeywords] = useState('5');
  const [llmConfig, setLlmConfig] = useState({ default_model: '', has_default_key: false });

  useEffect(() => {
    let cancelled = false;
    async function loadLlmConfig() {
      try {
        const response = await fetch('/api/llm/config');
        const payload = await response.json();
        if (!cancelled && response.ok) {
          setLlmConfig(payload);
        }
      } catch {
        if (!cancelled) {
          setLlmConfig({ default_model: '', has_default_key: false });
        }
      }
    }

    loadLlmConfig();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (provider === 'opendataloader') {
      setLlmEnabled(true);
    }
  }, [provider]);

  async function handleSubmit(event) {
    event.preventDefault();
    const representationOptions = {
      apiKey,
      keywordMinWords,
      llmEnabled,
      maxKeywords,
      model,
      quizMode,
      quizRepresentationChoice,
      summaryMinWords,
      summaryWordRatio,
    };
    if (selectedFile) {
      await onUploadSubmit(selectedFile, provider, representationOptions);
      return;
    }
    if (pdfUrl.trim()) {
      await onUrlSubmit(pdfUrl.trim(), provider, representationOptions);
    }
  }

  const requiresApiKey = llmEnabled && !llmConfig.has_default_key && !apiKey.trim();
  const canSubmit = Boolean(selectedFile || pdfUrl.trim()) && !isSubmitting && !requiresApiKey;

  return (
    <main className="landing-shell">
      <section className="landing-card">
        <p className="landing-kicker">Zoomable PDF Reader</p>
        <h1 className="landing-title">Upload a PDF or point to one on the web.</h1>
        <p className="landing-copy">
          The backend parses the document through a selectable provider, builds paragraph blocks for the zoomable document, and the reader maps block representations onto PDF overlays.
        </p>

        <form className="landing-form" onSubmit={handleSubmit}>
          <label className="field-card">
            <span className="field-label">Segmentation</span>
            <select
              className="text-input"
              onChange={(event) => setProvider(event.target.value)}
              value={provider}
            >
              <option value="native">Native parser</option>
              <option value="docling">Docling layout parser</option>
              <option value="grobid">GROBID paper parser</option>
              <option value="opendataloader">OpenDataLoader PDF</option>
            </select>
            <span className="field-hint">Choose a backend parser for semantic blocks, reading order, and source boxes.</span>
          </label>

          <fieldset className="field-card fieldset-card">
            <legend className="field-label">Representations</legend>
            <label className="inline-control">
              <input
                checked={llmEnabled}
                onChange={(event) => setLlmEnabled(event.target.checked)}
                type="checkbox"
              />
              <span>Use LLM</span>
            </label>

            <div className="settings-grid">
              <label>
                <span className="field-label">OpenAI key</span>
                <input
                  className="text-input"
                  disabled={!llmEnabled}
                  onChange={(event) => setApiKey(event.target.value)}
                  placeholder="Server default"
                  type="password"
                  value={apiKey}
                />
              </label>
              <label>
                <span className="field-label">Model</span>
                <input
                  className="text-input"
                  disabled={!llmEnabled}
                  onChange={(event) => setModel(event.target.value)}
                  placeholder={llmConfig.default_model || 'Server default'}
                  type="text"
                  value={model}
                />
              </label>
              <label>
                <span className="field-label">Keyword words</span>
                <input
                  className="text-input"
                  disabled={!llmEnabled}
                  min="1"
                  onChange={(event) => setKeywordMinWords(event.target.value)}
                  type="number"
                  value={keywordMinWords}
                />
              </label>
              <label>
                <span className="field-label">Summary words</span>
                <input
                  className="text-input"
                  disabled={!llmEnabled}
                  min="1"
                  onChange={(event) => setSummaryMinWords(event.target.value)}
                  type="number"
                  value={summaryMinWords}
                />
              </label>
              <label>
                <span className="field-label">Summary ratio</span>
                <input
                  className="text-input"
                  disabled={!llmEnabled}
                  max="0.8"
                  min="0.02"
                  onChange={(event) => setSummaryWordRatio(event.target.value)}
                  step="0.01"
                  type="number"
                  value={summaryWordRatio}
                />
              </label>
              <label>
                <span className="field-label">Max keywords</span>
                <input
                  className="text-input"
                  disabled={!llmEnabled}
                  min="1"
                  onChange={(event) => setMaxKeywords(event.target.value)}
                  type="number"
                  value={maxKeywords}
                />
              </label>
            </div>
            <span className="field-hint">
              {requiresApiKey ? 'Enter an OpenAI key to use the default LLM pipeline.' : 'Uses a request key or the server default key.'}
            </span>
          </fieldset>

          <label className="field-card">
            <span className="field-label">Upload PDF</span>
            <input
              accept="application/pdf"
              onChange={(event) => setSelectedFile(event.target.files?.[0] ?? null)}
              type="file"
            />
            <span className="field-hint">{selectedFile ? selectedFile.name : 'Choose a local PDF file.'}</span>
          </label>

          <label className="field-card">
            <span className="field-label">PDF link</span>
            <input
              className="text-input"
              onChange={(event) => setPdfUrl(event.target.value)}
              placeholder="https://example.com/paper.pdf"
              type="url"
              value={pdfUrl}
            />
            <span className="field-hint">Use this when the PDF is available by URL.</span>
          </label>

          <fieldset className="field-card fieldset-card">
            <legend className="field-label">Study Mode</legend>
            <label className="inline-control">
              <input
                checked={quizMode}
                onChange={(event) => setQuizMode(event.target.checked)}
                type="checkbox"
              />
              <span>Quiz Mode</span>
            </label>
            {quizMode && (
              <div className="quiz-representation-choice">
                <span className="field-label" style={{ fontSize: '0.8rem', marginBottom: '4px', display: 'block' }}>Representation</span>
                {[
                  { value: 'random', label: 'Random' },
                  { value: 'keywords', label: 'Keywords' },
                  { value: 'summary', label: 'Summary' },
                ].map(({ value, label }) => (
                  <label key={value} className="inline-control" style={{ marginBottom: '2px' }}>
                    <input
                      checked={quizRepresentationChoice === value}
                      name="quiz-representation"
                      onChange={() => setQuizRepresentationChoice(value)}
                      type="radio"
                      value={value}
                    />
                    <span>{label}</span>
                  </label>
                ))}
              </div>
            )}
            <span className="field-hint">
              {quizMode
                ? quizRepresentationChoice === 'random'
                  ? 'Starts a stopwatch and generates 3 comprehension questions. Representation is randomly assigned.'
                  : `Starts a stopwatch and generates 3 comprehension questions. Shows ${quizRepresentationChoice} representation.`
                : 'Starts a stopwatch and generates 3 comprehension questions.'}
            </span>
          </fieldset>

          {errorMessage ? <p className="error-banner">{errorMessage}</p> : null}

          <button className="primary-button" disabled={!canSubmit} type="submit">
            {isSubmitting ? (quizMode ? 'Generating quiz…' : 'Preparing reader…') : 'Confirm'}
          </button>
        </form>
      </section>
    </main>
  );
}
