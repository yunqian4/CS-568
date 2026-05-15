import { useEffect, useMemo, useState } from 'react';
import ReaderPage from './ReaderPage';
import { resetRepresentationSettings } from '../representationSettings';

const MANIFEST_URL = '/study-cache/manifest.json';
const PARTICIPANT_KEY = 'pdf-reader:exam-participant-id';

export default function ExamPage() {
  const [manifest, setManifest] = useState(null);
  const [assignment, setAssignment] = useState(null);
  const [documentData, setDocumentData] = useState(null);
  const [questions, setQuestions] = useState([]);
  const [step, setStep] = useState('loading');
  const [answers, setAnswers] = useState({});
  const [startedAtMs, setStartedAtMs] = useState(null);
  const [elapsed, setElapsed] = useState(0);
  const [errorMessage, setErrorMessage] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);

  useEffect(() => {
    let cancelled = false;

    async function loadExam() {
      try {
        const nextManifest = await fetchJson(MANIFEST_URL);
        const exam = pickScheduledExam(nextManifest);
        const documentEntry = nextManifest.documents.find((item) => item.document_id === exam.document_id);
        if (!documentEntry) {
          throw new Error('Assigned exam document is missing from the manifest.');
        }
        const [cachedDocument, questionSet] = await Promise.all([
          fetchJson(documentEntry.document_url),
          fetchJson(exam.question_url || `/study-cache/questions/${exam.question_set_id}.json`),
        ]);
        if (cancelled) return;
        setManifest(nextManifest);
        setAssignment({
          condition_id: exam.representation_condition?.id || 'default',
          document_id: exam.document_id,
          exam_id: exam.id,
          question_set_id: exam.question_set_id,
          representation_condition: exam.representation_condition ?? { visible_representations: [] },
          time_limit_seconds: Number(exam.time_limit_seconds) || 0,
          timing_mode: exam.timing_mode || (Number(exam.time_limit_seconds) > 0 ? 'countdown' : 'stopwatch'),
          title: exam.title || exam.id,
        });
        setDocumentData({
          ...cachedDocument,
          pdf_url: cachedDocument.pdf_url || `/study-cache/documents/${documentEntry.document_id}/source.pdf`,
        });
        setQuestions(questionSet.questions ?? []);
        setStep('ready');
      } catch (error) {
        if (!cancelled) {
          setErrorMessage(error.message || 'Unable to load exam.');
          setStep('error');
        }
      }
    }

    loadExam();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (step !== 'running' || !startedAtMs) {
      return undefined;
    }
    const intervalId = window.setInterval(() => {
      setElapsed(Math.floor((Date.now() - startedAtMs) / 1000));
    }, 500);
    return () => window.clearInterval(intervalId);
  }, [startedAtMs, step]);

  useEffect(() => {
    if (
      step !== 'running'
      || assignment?.timing_mode !== 'countdown'
      || !assignment?.time_limit_seconds
      || elapsed < assignment.time_limit_seconds
    ) {
      return;
    }
    submitExam({ autoSubmitted: true });
  }, [assignment?.time_limit_seconds, assignment?.timing_mode, elapsed, step]);

  const representationSettings = useMemo(
    () => buildExamRepresentationSettings(documentData, assignment?.representation_condition),
    [assignment?.representation_condition, documentData],
  );

  function startExam() {
    setStartedAtMs(Date.now());
    setElapsed(0);
    setStep('running');
  }

  async function submitExam({ autoSubmitted = false } = {}) {
    if (!assignment || !manifest || !startedAtMs || isSubmitting) return;

    setIsSubmitting(true);
    setErrorMessage('');
    try {
      const participantId = loadOrCreateParticipantId();
      const submittedAt = new Date().toISOString();
      const sessionId = createId('exam-session');
      const response = await fetch('/api/study/score-submit', {
        body: JSON.stringify({
          answers: questions.map((question) => ({
            question_id: question.id,
            selected_index: answers[question.id] ?? null,
          })),
          assignment,
          client: {
            language: navigator.language,
            timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
            user_agent: navigator.userAgent,
          },
          participant_id: participantId,
          session_id: sessionId,
          study_id: manifest.study_id,
          submitted_at: submittedAt,
          timing: {
            auto_submitted: autoSubmitted,
            elapsed_seconds: elapsed,
            started_at: new Date(startedAtMs).toISOString(),
            submitted_at: submittedAt,
            timing_mode: assignment.timing_mode,
          },
        }),
        headers: { 'Content-Type': 'application/json' },
        method: 'POST',
      });
      const payload = await readJsonResponse(response);
      if (!response.ok) {
        throw new Error(payload.detail || 'Unable to submit exam.');
      }
      window.alert('Answers submitted. This exam session has ended.');
      setStep('complete');
    } catch (error) {
      setErrorMessage(error.message || 'Unable to submit exam.');
    } finally {
      setIsSubmitting(false);
    }
  }

  if (step === 'loading') {
    return <ExamShell title="Loading exam" />;
  }

  if (step === 'error') {
    return <ExamShell title="Exam unavailable" errorMessage={errorMessage} />;
  }

  if (step === 'complete') {
    return (
      <ExamShell title="Exam complete">
        <p className="study-copy">Your answers were submitted. This session has ended.</p>
      </ExamShell>
    );
  }

  if (!manifest || !assignment || !documentData) {
    return <ExamShell title="Exam unavailable" errorMessage="Exam data is incomplete." />;
  }

  if (step === 'ready') {
    return (
      <ExamShell title={assignment.title || manifest.title}>
        <p className="study-copy">Click Start when you are ready to begin. The timer starts immediately.</p>
        <button className="primary-button" onClick={startExam} type="button">Start exam</button>
      </ExamShell>
    );
  }

  return (
    <ReaderPage
      document={documentData}
      onClearRepresentationCookie={() => {}}
      onLoadRepresentationCookie={() => {}}
      onRegenerateRepresentations={() => {}}
      onRepresentationSettingsChange={() => {}}
      onReset={() => { window.location.href = '/exam'; }}
      onResetRepresentationSettings={() => {}}
      onSaveRepresentationCookie={() => {}}
      quizMode
      quizPanel={(
        <ExamQuestionPanel
          answers={answers}
          assignment={assignment}
          elapsed={elapsed}
          errorMessage={errorMessage}
          isSubmitting={isSubmitting}
          onAnswerChange={(questionId, selectedIndex) => setAnswers((current) => ({ ...current, [questionId]: selectedIndex }))}
          onSubmit={() => submitExam()}
          questions={questions}
        />
      )}
      representationSettings={representationSettings}
      resetLabel="Restart exam"
      settingsMessage=""
    />
  );
}

function ExamQuestionPanel({
  answers,
  assignment,
  elapsed,
  errorMessage,
  isSubmitting,
  onAnswerChange,
  onSubmit,
  questions,
}) {
  const remaining = assignment.timing_mode === 'countdown'
    ? Math.max(Number(assignment.time_limit_seconds || 0) - elapsed, 0)
    : null;

  return (
    <aside className="quiz-panel">
      <div className="quiz-panel-header">
        <span className="quiz-panel-label">Exam</span>
        <span className="quiz-stopwatch">{formatTime(remaining ?? elapsed)}</span>
      </div>
      {errorMessage ? <p className="error-banner">{errorMessage}</p> : null}
      <div className="quiz-questions">
        {questions.map((question, questionIndex) => (
          <div className="quiz-question" key={question.id}>
            <p className="quiz-question-text">
              <span className="quiz-question-num">{questionIndex + 1}.</span> {question.text}
            </p>
            <div className="quiz-choices">
              {question.choices.map((choice, choiceIndex) => (
                <label className={`quiz-choice${answers[question.id] === choiceIndex ? ' quiz-choice-selected' : ''}`} key={`${question.id}-${choiceIndex}`}>
                  <input
                    checked={answers[question.id] === choiceIndex}
                    name={question.id}
                    onChange={() => onAnswerChange(question.id, choiceIndex)}
                    type="radio"
                    value={choiceIndex}
                  />
                  <span className="quiz-choice-letter">{String.fromCharCode(65 + choiceIndex)}</span>
                  <span className="quiz-choice-text">{choice}</span>
                </label>
              ))}
            </div>
          </div>
        ))}
      </div>
      <button className="primary-button quiz-submit-button" disabled={!questions.length || isSubmitting} onClick={onSubmit} type="button">
        {isSubmitting ? 'Submitting...' : 'Submit answers'}
      </button>
    </aside>
  );
}

function ExamShell({ children = null, errorMessage = '', title }) {
  return (
    <main className="study-shell">
      <section className="study-card">
        <p className="landing-kicker">Exam</p>
        <h1 className="landing-title">{title}</h1>
        {errorMessage ? <p className="error-banner">{errorMessage}</p> : null}
        {children}
      </section>
    </main>
  );
}

function buildExamRepresentationSettings(document, condition) {
  const visible = new Set(condition?.visible_representations ?? []);
  const definitions = collectRepresentationDefinitions(document);
  if (!definitions.length) {
    return resetRepresentationSettings().map((setting) => ({
      ...setting,
      enabled: visible.has(setting.id) || visible.has(setting.name),
    }));
  }
  return definitions.map((definition, index) => {
    const name = String(definition.name || definition.kind || `representation-${index + 1}`);
    return {
      background_color: definition.background_color || '#263238',
      background_opacity: Number(definition.background_opacity ?? 1),
      enabled: visible.has(name) || visible.has(definition.kind),
      id: name,
      isDefault: name === 'keywords' || name === 'summary',
      name,
      prompt: definition.prompt || '',
    };
  });
}

function collectRepresentationDefinitions(document) {
  const byKind = new Map();
  for (const block of document?.blocks ?? []) {
    for (const representation of block.representations ?? []) {
      if (representation?.kind && !byKind.has(representation.kind)) {
        byKind.set(representation.kind, {
          ...representation,
          name: representation.kind,
        });
      }
    }
  }
  return [...byKind.values()];
}

function pickScheduledExam(manifest) {
  const exams = manifest.exam_settings ?? [];
  if (!exams.length) throw new Error('Study manifest has no exam settings.');
  const schedulerItems = manifest.scheduler?.exam_settings ?? [];
  const weighted = schedulerItems
    .map((item) => ({
      exam: exams.find((exam) => exam.id === item.exam_id),
      weight: Math.max(Number(item.weight) || 0, 0),
    }))
    .filter((item) => item.exam && item.weight > 0);
  if (!weighted.length) return pickRandom(exams);
  const total = weighted.reduce((sum, item) => sum + item.weight, 0);
  let threshold = Math.random() * total;
  for (const item of weighted) {
    threshold -= item.weight;
    if (threshold <= 0) return item.exam;
  }
  return weighted[weighted.length - 1].exam;
}

function loadOrCreateParticipantId() {
  const stored = localStorage.getItem(PARTICIPANT_KEY);
  if (stored) return stored;
  const participantId = createId('participant');
  localStorage.setItem(PARTICIPANT_KEY, participantId);
  return participantId;
}

async function fetchJson(url) {
  const response = await fetch(url);
  const payload = await readJsonResponse(response);
  if (!response.ok) throw new Error(payload.detail || `Unable to load ${url}.`);
  return payload;
}

async function readJsonResponse(response) {
  const text = await response.text();
  if (!text) return {};
  try {
    return JSON.parse(text);
  } catch {
    return { detail: text };
  }
}

function pickRandom(items) {
  if (!items?.length) throw new Error('Study manifest has no assignable exams.');
  return items[Math.floor(Math.random() * items.length)];
}

function createId(prefix) {
  const value = crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `${prefix}-${value}`;
}

function formatTime(seconds) {
  const safeSeconds = Math.max(Number(seconds) || 0, 0);
  const minutes = Math.floor(safeSeconds / 60);
  const remainder = safeSeconds % 60;
  return `${minutes}:${String(remainder).padStart(2, '0')}`;
}
