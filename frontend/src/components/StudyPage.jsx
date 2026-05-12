import { useEffect, useMemo, useState } from 'react';
import ReaderPage from './ReaderPage';
import { resetRepresentationSettings } from '../representationSettings';

const MANIFEST_URL = '/study-cache/manifest.json';
const PARTICIPANT_KEY = 'pdf-reader:study-participant-id';
const SESSION_KEY = 'pdf-reader:study-session';

export default function StudyPage() {
  const [manifest, setManifest] = useState(null);
  const [session, setSession] = useState(null);
  const [documentData, setDocumentData] = useState(null);
  const [questions, setQuestions] = useState([]);
  const [step, setStep] = useState('loading');
  const [preResponses, setPreResponses] = useState({});
  const [postResponses, setPostResponses] = useState({});
  const [answers, setAnswers] = useState([]);
  const [timing, setTiming] = useState({});
  const [errorMessage, setErrorMessage] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);

  useEffect(() => {
    let cancelled = false;

    async function loadStudy() {
      try {
        const nextManifest = await fetchJson(MANIFEST_URL);
        const nextSession = loadOrCreateSession(nextManifest);
        const documentEntry = nextManifest.documents.find(
          (item) => item.document_id === nextSession.assignment.document_id,
        );
        if (!documentEntry) {
          throw new Error('Assigned study document is missing from the manifest.');
        }

        const [cachedDocument, questionSet] = await Promise.all([
          fetchJson(documentEntry.document_url),
          fetchJson(nextSession.assignment.question_url),
        ]);

        if (cancelled) return;
        setManifest(nextManifest);
        setSession(nextSession);
        setDocumentData({
          ...cachedDocument,
          pdf_url: cachedDocument.pdf_url || `/study-cache/documents/${documentEntry.document_id}/source.pdf`,
        });
        setQuestions(questionSet.questions ?? []);
        setStep('pre');
      } catch (error) {
        if (!cancelled) {
          setErrorMessage(error.message || 'Unable to load the study.');
          setStep('error');
        }
      }
    }

    loadStudy();
    return () => {
      cancelled = true;
    };
  }, []);

  const condition = useMemo(() => session?.assignment?.representation_condition ?? null, [session]);

  const questionnaires = useMemo(
    () => session?.assignment?.questionnaires ?? manifest?.questionnaires ?? {},
    [manifest, session],
  );

  const representationSettings = useMemo(
    () => buildStudyRepresentationSettings(condition),
    [condition],
  );

  function startReading(nextPreResponses) {
    setPreResponses(nextPreResponses);
    setTiming((current) => ({
      ...current,
      reading_started_at: new Date().toISOString(),
      reading_started_ms: Date.now(),
    }));
    setStep('read');
  }

  function handleExamSubmit(nextAnswers, elapsedSeconds) {
    setAnswers(nextAnswers);
    setTiming((current) => ({
      ...current,
      exam_elapsed_seconds: elapsedSeconds,
      post_started_at: new Date().toISOString(),
    }));
    setStep('post');
  }

  async function submitStudy(nextPostResponses) {
    setIsSubmitting(true);
    setErrorMessage('');
    setPostResponses(nextPostResponses);
    try {
      const submittedAt = new Date().toISOString();
      const response = await fetch('/api/study/score-submit', {
        body: JSON.stringify({
          answers,
          assignment: session.assignment,
          client: {
            language: navigator.language,
            timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
            user_agent: navigator.userAgent,
          },
          participant_id: session.participant_id,
          post_responses: nextPostResponses,
          pre_responses: preResponses,
          session_id: session.session_id,
          study_id: manifest.study_id,
          submitted_at: submittedAt,
          timing: {
            ...timing,
            submitted_at: submittedAt,
            total_elapsed_seconds: timing.reading_started_ms
              ? Math.round((Date.now() - timing.reading_started_ms) / 1000)
              : null,
          },
        }),
        headers: { 'Content-Type': 'application/json' },
        method: 'POST',
      });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.detail || 'Unable to submit study results.');
      }
      sessionStorage.removeItem(SESSION_KEY);
      setStep('complete');
    } catch (error) {
      setErrorMessage(error.message || 'Unable to submit study results.');
    } finally {
      setIsSubmitting(false);
    }
  }

  function resetStudySession() {
    sessionStorage.removeItem(SESSION_KEY);
    window.location.href = '/study';
  }

  if (step === 'loading') {
    return <StudyShell title="Loading study" />;
  }

  if (step === 'error') {
    return <StudyShell title="Study unavailable" errorMessage={errorMessage} />;
  }

  if (step === 'complete') {
    return (
      <StudyShell title="Study complete">
        <p className="study-copy">Your responses were recorded.</p>
      </StudyShell>
    );
  }

  if (!manifest || !session || !documentData || !condition) {
    return <StudyShell title="Study unavailable" errorMessage="Study data is incomplete." />;
  }

  if (step === 'pre') {
    return (
      <StudyShell title={manifest.title}>
        <QuestionnaireForm
          fields={questionnaires?.pre ?? []}
          onSubmit={startReading}
          submitLabel="Start reading"
        />
      </StudyShell>
    );
  }

  if (step === 'post') {
    return (
      <StudyShell title="Final questions" errorMessage={errorMessage}>
        <QuestionnaireForm
          fields={questionnaires?.post ?? []}
          isSubmitting={isSubmitting}
          onSubmit={submitStudy}
          submitLabel={isSubmitting ? 'Submitting...' : 'Submit study'}
        />
      </StudyShell>
    );
  }

  const quizPanel = (
    <StudyExamPanel
      onSubmit={handleExamSubmit}
      questions={questions}
      startTime={timing.reading_started_ms || Date.now()}
      timeLimitSeconds={session.assignment.time_limit_seconds}
    />
  );

  return (
    <ReaderPage
      document={documentData}
      onClearRepresentationCookie={() => {}}
      onLoadRepresentationCookie={() => {}}
      onRegenerateRepresentations={() => {}}
      onRepresentationSettingsChange={() => {}}
      onReset={resetStudySession}
      onResetRepresentationSettings={() => {}}
      onSaveRepresentationCookie={() => {}}
      quizMode
      quizPanel={quizPanel}
      representationSettings={representationSettings}
      resetLabel="Restart study"
      settingsMessage=""
    />
  );
}

function StudyShell({ children = null, errorMessage = '', title }) {
  return (
    <main className="study-shell">
      <section className="study-card">
        <p className="landing-kicker">Study</p>
        <h1 className="landing-title">{title}</h1>
        {errorMessage ? <p className="error-banner">{errorMessage}</p> : null}
        {children}
      </section>
    </main>
  );
}

function QuestionnaireForm({ fields, isSubmitting = false, onSubmit, submitLabel }) {
  const [responses, setResponses] = useState({});
  const canSubmit = fields.every((field) => !field.required || hasValue(responses[field.id]));

  function updateResponse(id, value) {
    setResponses((current) => ({ ...current, [id]: value }));
  }

  function handleSubmit(event) {
    event.preventDefault();
    if (canSubmit) {
      onSubmit(responses);
    }
  }

  return (
    <form className="study-form" onSubmit={handleSubmit}>
      {fields.map((field) => (
        <StudyField
          field={field}
          key={field.id}
          onChange={(value) => updateResponse(field.id, value)}
          value={responses[field.id] ?? ''}
        />
      ))}
      <button className="primary-button" disabled={!canSubmit || isSubmitting} type="submit">
        {submitLabel}
      </button>
    </form>
  );
}

function StudyField({ field, onChange, value }) {
  if (field.type === 'radio') {
    return (
      <fieldset className="study-field">
        <legend className="field-label">{field.label}</legend>
        <div className="study-options">
          {(field.options ?? []).map((option) => (
            <label className="inline-control" key={option}>
              <input
                checked={value === option}
                name={field.id}
                onChange={() => onChange(option)}
                type="radio"
                value={option}
              />
              <span>{option}</span>
            </label>
          ))}
        </div>
      </fieldset>
    );
  }

  if (field.type === 'textarea') {
    return (
      <label className="study-field">
        <span className="field-label">{field.label}</span>
        <textarea
          className="text-input"
          onChange={(event) => onChange(event.target.value)}
          rows={4}
          value={value}
        />
      </label>
    );
  }

  if (field.type === 'likert') {
    const min = Number(field.min ?? 1);
    const max = Number(field.max ?? 5);
    return (
      <fieldset className="study-field">
        <legend className="field-label">{field.label}</legend>
        <div className="study-likert">
          {range(min, max).map((number) => (
            <label className="study-likert-option" key={number}>
              <input
                checked={Number(value) === number}
                name={field.id}
                onChange={() => onChange(number)}
                type="radio"
                value={number}
              />
              <span>{number}</span>
            </label>
          ))}
        </div>
      </fieldset>
    );
  }

  return (
    <label className="study-field">
      <span className="field-label">{field.label}</span>
      <input
        className="text-input"
        onChange={(event) => onChange(event.target.value)}
        type="text"
        value={value}
      />
    </label>
  );
}

function StudyExamPanel({ onSubmit, questions, startTime, timeLimitSeconds = 0 }) {
  const [answers, setAnswers] = useState({});
  const [elapsed, setElapsed] = useState(0);
  const [submitted, setSubmitted] = useState(false);

  useEffect(() => {
    const intervalId = window.setInterval(() => {
      setElapsed(Math.floor((Date.now() - startTime) / 1000));
    }, 500);
    return () => window.clearInterval(intervalId);
  }, [startTime]);

  useEffect(() => {
    if (submitted || !timeLimitSeconds || elapsed < timeLimitSeconds) {
      return;
    }
    submitAnswers(true);
  }, [elapsed, submitted, timeLimitSeconds]);

  const allAnswered = questions.length > 0 && questions.every((question) => answers[question.id] !== undefined);

  function submitAnswers(allowIncomplete = false) {
    if (submitted || (!allowIncomplete && !allAnswered)) {
      return;
    }
    setSubmitted(true);
    onSubmit(
      questions.map((question) => ({
        question_id: question.id,
        selected_index: answers[question.id] ?? null,
      })),
      elapsed,
    );
  }

  const remaining = timeLimitSeconds ? Math.max(timeLimitSeconds - elapsed, 0) : null;

  return (
    <aside className="quiz-panel">
      <div className="quiz-panel-header">
        <span className="quiz-panel-label">Exam</span>
        <span className="quiz-stopwatch">
          {remaining == null ? formatTime(elapsed) : formatTime(remaining)}
        </span>
      </div>
      <div className="quiz-questions">
        {questions.map((question, questionIndex) => (
          <div className="quiz-question" key={question.id}>
            <p className="quiz-question-text">
              <span className="quiz-question-num">{questionIndex + 1}.</span> {question.text}
            </p>
            <div className="quiz-choices">
              {question.choices.map((choice, choiceIndex) => (
                <label
                  className={`quiz-choice${answers[question.id] === choiceIndex ? ' quiz-choice-selected' : ''}`}
                  key={`${question.id}-${choiceIndex}`}
                >
                  <input
                    checked={answers[question.id] === choiceIndex}
                    name={question.id}
                    onChange={() => setAnswers((current) => ({ ...current, [question.id]: choiceIndex }))}
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
      <button className="primary-button quiz-submit-button" disabled={!allAnswered || submitted} onClick={() => submitAnswers()} type="button">
        Finish exam
      </button>
    </aside>
  );
}

function buildStudyRepresentationSettings(condition) {
  const visible = new Set(condition?.visible_representations ?? []);
  return resetRepresentationSettings().map((setting) => ({
    ...setting,
    enabled: visible.has(setting.id) || visible.has(setting.name),
  }));
}

function loadOrCreateSession(manifest) {
  const stored = readStoredSession();
  if (stored?.study_id === manifest.study_id && isValidAssignment(stored.assignment, manifest)) {
    return stored;
  }

  const participantId = loadOrCreateParticipantId();
  const exam = pickScheduledExam(manifest);
  const session = {
    assignment: {
      condition_id: exam.representation_condition?.id || 'default',
      document_id: exam.document_id,
      exam_id: exam.id,
      question_set_id: exam.question_set_id,
      question_url: exam.question_url || `/study-cache/questions/${exam.question_set_id}.json`,
      questionnaires: exam.questionnaires ?? {},
      representation_condition: exam.representation_condition ?? { visible_representations: [] },
      time_limit_seconds: exam.time_limit_seconds || 0,
    },
    participant_id: participantId,
    session_id: createId('session'),
    study_id: manifest.study_id,
  };
  sessionStorage.setItem(SESSION_KEY, JSON.stringify(session));
  return session;
}

function isValidAssignment(assignment, manifest) {
  return Boolean(
    assignment
    && manifest.documents.some((item) => item.document_id === assignment.document_id)
    && manifest.exam_settings?.some((item) => item.id === assignment.exam_id),
  );
}

function pickScheduledExam(manifest) {
  const exams = manifest.exam_settings ?? [];
  if (!exams.length) {
    throw new Error('Study manifest has no exam settings.');
  }

  const schedulerItems = manifest.scheduler?.exam_settings ?? [];
  const weighted = schedulerItems
    .map((item) => ({
      exam: exams.find((exam) => exam.id === item.exam_id),
      weight: Math.max(Number(item.weight) || 0, 0),
    }))
    .filter((item) => item.exam && item.weight > 0);

  if (!weighted.length) {
    return pickRandom(exams);
  }

  const total = weighted.reduce((sum, item) => sum + item.weight, 0);
  let threshold = Math.random() * total;
  for (const item of weighted) {
    threshold -= item.weight;
    if (threshold <= 0) {
      return item.exam;
    }
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

function readStoredSession() {
  try {
    return JSON.parse(sessionStorage.getItem(SESSION_KEY));
  } catch {
    sessionStorage.removeItem(SESSION_KEY);
    return null;
  }
}

async function fetchJson(url) {
  const response = await fetch(url);
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.detail || `Unable to load ${url}.`);
  }
  return payload;
}

function pickRandom(items) {
  if (!items?.length) {
    throw new Error('Study manifest has no assignable items.');
  }
  return items[Math.floor(Math.random() * items.length)];
}

function createId(prefix) {
  const value = crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `${prefix}-${value}`;
}

function hasValue(value) {
  return value !== undefined && value !== null && value !== '';
}

function range(min, max) {
  return Array.from({ length: max - min + 1 }, (_, index) => min + index);
}

function formatTime(seconds) {
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return `${minutes}:${String(remainder).padStart(2, '0')}`;
}
