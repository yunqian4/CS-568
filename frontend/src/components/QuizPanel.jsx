import { useEffect, useRef, useState } from 'react';

const LETTERS = ['A', 'B', 'C', 'D', 'E'];

export default function QuizPanel({ fetchError, onSubmit, questions, startTime }) {
  const [answers, setAnswers] = useState({});
  const [elapsed, setElapsed] = useState(0);
  const intervalRef = useRef(null);

  useEffect(() => {
    setElapsed(Math.floor((Date.now() - startTime) / 1000));
    intervalRef.current = setInterval(() => {
      setElapsed(Math.floor((Date.now() - startTime) / 1000));
    }, 500);
    return () => clearInterval(intervalRef.current);
  }, [startTime]);

  function formatTime(seconds) {
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    return `${m}:${String(s).padStart(2, '0')}`;
  }

  function handleSubmit() {
    if (!questions?.length) return;
    const score = questions.filter((q, i) => answers[i] === q.answer_index).length;
    clearInterval(intervalRef.current);
    onSubmit({ score, totalQuestions: questions.length, timeSeconds: elapsed });
  }

  const allAnswered = questions?.length > 0 && questions.every((_, i) => answers[i] !== undefined);

  return (
    <aside className="quiz-panel">
      <div className="quiz-panel-header">
        <span className="quiz-panel-label">Quiz</span>
        <span className="quiz-stopwatch">{formatTime(elapsed)}</span>
      </div>

      {fetchError && <p className="error-banner">{fetchError}</p>}

      {questions?.length > 0 && (
        <div className="quiz-questions">
          {questions.map((q, qi) => (
            <div key={qi} className="quiz-question">
              <p className="quiz-question-text">
                <span className="quiz-question-num">{qi + 1}.</span>
                {' '}
                {q.text}
              </p>
              <div className="quiz-choices">
                {q.choices.map((choice, ci) => (
                  <label
                    key={ci}
                    className={`quiz-choice${answers[qi] === ci ? ' quiz-choice-selected' : ''}`}
                  >
                    <input
                      checked={answers[qi] === ci}
                      name={`q-${qi}`}
                      onChange={() => setAnswers((prev) => ({ ...prev, [qi]: ci }))}
                      type="radio"
                      value={ci}
                    />
                    <span className="quiz-choice-letter">{LETTERS[ci]}</span>
                    <span className="quiz-choice-text">{choice}</span>
                  </label>
                ))}
              </div>
            </div>
          ))}

          <button
            className="primary-button quiz-submit-button"
            disabled={!allAnswered}
            onClick={handleSubmit}
            type="button"
          >
            Submit Answers
          </button>
        </div>
      )}

      {questions?.length === 0 && !fetchError && (
        <p className="quiz-status">No questions could be generated for this document.</p>
      )}
    </aside>
  );
}
