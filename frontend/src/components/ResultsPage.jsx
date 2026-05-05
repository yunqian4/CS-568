export default function ResultsPage({
  articleName,
  onReset,
  representationName,
  score,
  timeSeconds,
  totalQuestions,
}) {
  function formatTime(s) {
    const m = Math.floor(s / 60);
    const sec = s % 60;
    return `${m}:${String(sec).padStart(2, '0')}`;
  }

  const percentage = totalQuestions > 0 ? Math.round((score / totalQuestions) * 100) : 0;

  return (
    <main className="results-shell">
      <section className="results-card">
        <p className="landing-kicker">Quiz Complete</p>

        <div className="results-score-block">
          <span className="results-score">{score}/{totalQuestions}</span>
          <span className="results-percent">{percentage}%</span>
        </div>

        <div className="results-meta">
          <div className="results-meta-item">
            <span className="results-meta-label">Time</span>
            <span className="results-meta-value">{formatTime(timeSeconds)}</span>
          </div>
          <div className="results-meta-item">
            <span className="results-meta-label">Article</span>
            <span className="results-meta-value results-meta-article" title={articleName}>
              {articleName}
            </span>
          </div>
          <div className="results-meta-item">
            <span className="results-meta-label">Representation</span>
            <span className="results-meta-value results-meta-rep">{representationName}</span>
          </div>
        </div>

        <button className="primary-button results-reset-button" onClick={onReset} type="button">
          Try Another PDF
        </button>
      </section>
    </main>
  );
}
