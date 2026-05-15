import { useEffect, useState } from 'react';

const EXAMS_URL = '/api/human-study/exams';
const BACKEND_DOCUMENTS_URL = '/api/human-study/documents';

const EMPTY_QUESTION = {
  answer_index: 0,
  choices: ['Choice 1', 'Choice 2', 'Choice 3', 'Choice 4'],
  id: '',
  text: '',
};

export default function ExamDesignerPage() {
  const [documents, setDocuments] = useState([]);
  const [exams, setExams] = useState([]);
  const [exam, setExam] = useState(emptyExam());
  const [representationOptions, setRepresentationOptions] = useState(['keywords', 'summary']);
  const [selectedExamId, setSelectedExamId] = useState('');
  const [statusMessage, setStatusMessage] = useState('');
  const [errorMessage, setErrorMessage] = useState('');

  useEffect(() => {
    loadIndex();
  }, []);

  useEffect(() => {
    if (!exam.document_id) {
      setRepresentationOptions(['keywords', 'summary']);
      return;
    }
    loadRepresentationOptions(exam.document_id);
  }, [exam.document_id]);

  async function loadIndex() {
    setErrorMessage('');
    try {
      const payload = await fetchJson(EXAMS_URL);
      setDocuments(payload.documents ?? []);
      setExams(payload.exams ?? []);
      if (payload.exams?.length) {
        setSelectedExamId(payload.exams[0].id);
        setExam(normalizeExam(payload.exams[0]));
      } else {
        setExam(emptyExam(payload.documents?.[0]?.document_id));
      }
    } catch (error) {
      setErrorMessage(error.message || 'Unable to load exams.');
    }
  }

  function selectExam(examId) {
    if (!examId) {
      createNewExam();
      return;
    }
    setSelectedExamId(examId);
    const nextExam = exams.find((item) => item.id === examId);
    if (nextExam) {
      setExam(normalizeExam(nextExam));
    }
  }

  function createNewExam() {
    setSelectedExamId('');
    setExam(emptyExam(documents[0]?.document_id));
    setStatusMessage('');
    setErrorMessage('');
  }

  function patchExam(patch) {
    setExam((current) => normalizeExam({ ...current, ...patch }));
  }

  function patchCondition(patch) {
    setExam((current) => normalizeExam({
      ...current,
      representation_condition: {
        ...current.representation_condition,
        ...patch,
      },
    }));
  }

  async function loadRepresentationOptions(documentId) {
    try {
      const payload = await fetchJson(`${BACKEND_DOCUMENTS_URL}/${encodeURIComponent(documentId)}`);
      setRepresentationOptions(collectRepresentationOptions(payload));
    } catch {
      const selected = exam.representation_condition?.visible_representations ?? [];
      setRepresentationOptions(uniqueNonEmpty([...selected, 'keywords', 'summary']));
    }
  }

  function toggleVisibleRepresentation(name) {
    const current = new Set(exam.representation_condition?.visible_representations ?? []);
    if (current.has(name)) {
      current.delete(name);
    } else {
      current.add(name);
    }
    const visible = [...current];
    patchCondition({
      id: visible.length ? visible.join('-') : 'none',
      label: visible.length ? visible.join(', ') : 'None',
      visible_representations: visible,
    });
  }

  function patchQuestion(index, patch) {
    setExam((current) => normalizeExam({
      ...current,
      questions: current.questions.map((question, questionIndex) => (
        questionIndex === index ? { ...question, ...patch } : question
      )),
    }));
  }

  function patchChoice(questionIndex, choiceIndex, value) {
    setExam((current) => normalizeExam({
      ...current,
      questions: current.questions.map((question, index) => {
        if (index !== questionIndex) return question;
        return {
          ...question,
          choices: question.choices.map((choice, nextChoiceIndex) => (
            nextChoiceIndex === choiceIndex ? value : choice
          )),
        };
      }),
    }));
  }

  function addQuestion() {
    setExam((current) => normalizeExam({
      ...current,
      questions: [
        ...current.questions,
        {
          ...EMPTY_QUESTION,
          text: `Question ${current.questions.length + 1}`,
        },
      ],
    }));
  }

  function removeQuestion(index) {
    setExam((current) => normalizeExam({
      ...current,
      questions: current.questions.filter((_, questionIndex) => questionIndex !== index),
    }));
  }

  function addChoice(questionIndex) {
    setExam((current) => normalizeExam({
      ...current,
      questions: current.questions.map((question, index) => (
        index === questionIndex
          ? { ...question, choices: [...question.choices, `Choice ${question.choices.length + 1}`] }
          : question
      )),
    }));
  }

  function removeChoice(questionIndex, choiceIndex) {
    setExam((current) => normalizeExam({
      ...current,
      questions: current.questions.map((question, index) => {
        if (index !== questionIndex) return question;
        const choices = question.choices.filter((_, nextChoiceIndex) => nextChoiceIndex !== choiceIndex);
        return {
          ...question,
          answer_index: Math.min(question.answer_index, Math.max(choices.length - 1, 0)),
          choices: choices.length ? choices : ['Choice 1'],
        };
      }),
    }));
  }

  async function saveExam(event) {
    event.preventDefault();
    setErrorMessage('');
    const normalized = normalizeExam(exam);
    if (!normalized.id || !normalized.document_id) {
      setErrorMessage('Exam id and document are required.');
      return;
    }
    try {
      const response = await fetch(`${EXAMS_URL}/${encodeURIComponent(normalized.id)}`, {
        body: JSON.stringify({ exam: normalized }),
        headers: { 'Content-Type': 'application/json' },
        method: 'POST',
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || 'Unable to save exam.');
      setSelectedExamId(payload.exam.id);
      setExam(normalizeExam(payload.exam));
      setExams((current) => upsertById(current, payload.exam));
      setStatusMessage(`Exam saved: ${payload.saved_path}`);
      window.alert(`Exam saved:\n${payload.saved_path}`);
    } catch (error) {
      setErrorMessage(error.message || 'Unable to save exam.');
    }
  }

  async function deleteExam() {
    if (!selectedExamId) return;
    const confirmed = window.confirm(`Delete exam ${selectedExamId}?`);
    if (!confirmed) return;
    try {
      const response = await fetch(`${EXAMS_URL}/${encodeURIComponent(selectedExamId)}`, { method: 'DELETE' });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || 'Unable to delete exam.');
      const nextExams = exams.filter((item) => item.id !== selectedExamId);
      setExams(nextExams);
      setSelectedExamId(nextExams[0]?.id ?? '');
      setExam(nextExams[0] ? normalizeExam(nextExams[0]) : emptyExam(documents[0]?.document_id));
      setStatusMessage(`Exam deleted: ${payload.deleted_path}`);
    } catch (error) {
      setErrorMessage(error.message || 'Unable to delete exam.');
    }
  }

  return (
    <main className="study-shell">
      <section className="exam-designer-shell">
        <div>
          <p className="landing-kicker">Exam designer</p>
          <h1 className="landing-title">Design cached exams</h1>
        </div>
        {statusMessage ? <p className="reader-settings-status">{statusMessage}</p> : null}
        {errorMessage ? <p className="error-banner">{errorMessage}</p> : null}

        <div className="designer-panel-header">
          <label className="settings-field">
            <span>Current exam</span>
            <select className="text-input" onChange={(event) => selectExam(event.target.value)} value={selectedExamId}>
              <option value="">New exam</option>
              {exams.map((item) => (
                <option key={item.id} value={item.id}>{item.title || item.id}</option>
              ))}
            </select>
          </label>
          <button className="secondary-button" onClick={createNewExam} type="button">New</button>
          <button className="settings-mini-button" disabled={!selectedExamId} onClick={deleteExam} type="button">Delete</button>
        </div>

        <form className="exam-designer-form" onSubmit={saveExam}>
          <section className="designer-card">
            <div className="designer-section-heading">
              <strong>Exam settings</strong>
              <label className="inline-control">
                <input checked={exam.enabled} onChange={(event) => patchExam({ enabled: event.target.checked })} type="checkbox" />
                <span>Enabled</span>
              </label>
            </div>
            <div className="exam-grid">
              <label className="settings-field">
                <span>Exam id</span>
                <input className="text-input" onChange={(event) => patchExam({ id: safeId(event.target.value) })} required type="text" value={exam.id} />
              </label>
              <label className="settings-field">
                <span>Title</span>
                <input className="text-input" onChange={(event) => patchExam({ title: event.target.value })} type="text" value={exam.title} />
              </label>
              <label className="settings-field">
                <span>Document</span>
                <select className="text-input" onChange={(event) => patchExam({ document_id: event.target.value })} required value={exam.document_id}>
                  <option value="">Choose document</option>
                  {documents.map((document) => (
                    <option key={document.document_id} value={document.document_id}>
                      {document.title || document.document_id}
                    </option>
                  ))}
                </select>
              </label>
              <label className="settings-field">
                <span>Timer mode</span>
                <select className="text-input" onChange={(event) => patchExam({ timing_mode: event.target.value })} value={exam.timing_mode}>
                  <option value="countdown">Countdown</option>
                  <option value="stopwatch">Record reading time from 0</option>
                </select>
              </label>
              {exam.timing_mode === 'countdown' ? (
                <label className="settings-field">
                  <span>Time limit seconds</span>
                  <input className="text-input" min="1" onChange={(event) => patchExam({ time_limit_seconds: Number(event.target.value) })} type="number" value={exam.time_limit_seconds} />
                </label>
              ) : null}
              <label className="settings-field">
                <span>Question set id</span>
                <input className="text-input" onChange={(event) => patchExam({ question_set_id: safeId(event.target.value) })} type="text" value={exam.question_set_id} />
              </label>
            </div>
          </section>

          <section className="designer-card">
            <strong>Visible representations</strong>
            <div className="exam-representation-options">
              {representationOptions.map((name) => (
                <label className="inline-control" key={name}>
                  <input
                    checked={(exam.representation_condition?.visible_representations ?? []).includes(name)}
                    onChange={() => toggleVisibleRepresentation(name)}
                    type="checkbox"
                  />
                  <span>{name}</span>
                </label>
              ))}
            </div>
          </section>

          <section className="designer-card">
            <div className="designer-section-heading">
              <strong>Multiple choice questions</strong>
              <button className="settings-mini-button" onClick={addQuestion} type="button">Add question</button>
            </div>
            <div className="designer-card-list">
              {exam.questions.map((question, questionIndex) => (
                <section className="designer-card" key={`${question.id}-${questionIndex}`}>
                  <div className="designer-card-row">
                    <div>
                      <strong>Question {questionIndex + 1}</strong>
                      <p className="designer-helper-text">ID: {autoQuestionId(exam.id, questionIndex)}</p>
                    </div>
                    <button className="settings-mini-button" onClick={() => removeQuestion(questionIndex)} type="button">Remove</button>
                  </div>
                  <label className="settings-field">
                    <span>Question text</span>
                    <textarea className="text-input designer-textarea" onChange={(event) => patchQuestion(questionIndex, { text: event.target.value })} value={question.text} />
                  </label>
                  <div className="designer-card-list">
                    {question.choices.map((choice, choiceIndex) => (
                      <div className="exam-choice-row" key={`${question.id}-${choiceIndex}`}>
                        <label className="inline-control">
                          <input
                            checked={question.answer_index === choiceIndex}
                            name={`${question.id}-answer`}
                            onChange={() => patchQuestion(questionIndex, { answer_index: choiceIndex })}
                            type="radio"
                          />
                          <span>{String.fromCharCode(65 + choiceIndex)}</span>
                        </label>
                        <input className="text-input" onChange={(event) => patchChoice(questionIndex, choiceIndex, event.target.value)} type="text" value={choice} />
                        <button className="settings-mini-button" onClick={() => removeChoice(questionIndex, choiceIndex)} type="button">Remove</button>
                      </div>
                    ))}
                  </div>
                  <button className="settings-mini-button" onClick={() => addChoice(questionIndex)} type="button">Add choice</button>
                </section>
              ))}
            </div>
          </section>

          <button className="primary-button" type="submit">Save exam</button>
        </form>
      </section>
    </main>
  );
}

function emptyExam(documentId = '') {
  return normalizeExam({
    document_id: documentId,
    enabled: true,
    id: 'new-exam',
    questions: [{ ...EMPTY_QUESTION, text: 'Question 1' }],
    representation_condition: { id: 'none', label: 'None', visible_representations: [] },
    time_limit_seconds: 900,
    timing_mode: 'countdown',
    title: 'New Exam',
  });
}

function normalizeExam(exam) {
  const id = safeId(exam.id || 'new-exam');
  const timingMode = exam.timing_mode === 'stopwatch' ? 'stopwatch' : 'countdown';
  return {
    ...exam,
    document_id: exam.document_id || '',
    enabled: exam.enabled !== false,
    id,
    question_set_id: safeId(exam.question_set_id || `${id}-questions`),
    questions: (exam.questions?.length ? exam.questions : [{ ...EMPTY_QUESTION }]).map((question, index) => {
      const choices = question.choices?.length ? question.choices : ['Choice 1', 'Choice 2'];
      return {
        answer_index: Math.min(Math.max(Number(question.answer_index) || 0, 0), choices.length - 1),
        choices,
        id: autoQuestionId(id, index),
        text: question.text || '',
      };
    }),
    representation_condition: exam.representation_condition ?? { id: 'none', label: 'None', visible_representations: [] },
    time_limit_seconds: timingMode === 'countdown' ? Math.max(Number(exam.time_limit_seconds) || 900, 1) : 0,
    timing_mode: timingMode,
    title: exam.title || id,
  };
}

function safeId(value) {
  return String(value || '').toLowerCase().replace(/[^a-z0-9_.-]+/g, '-').replace(/^-+|-+$/g, '');
}

function autoQuestionId(examId, questionIndex) {
  return `${safeId(examId || 'exam')}-q${questionIndex + 1}`;
}

function collectRepresentationOptions(payload) {
  const names = [];
  const promptDefinitions = payload.representation_prompts?.representations
    ?? payload.config?.llm?.representations
    ?? payload.document?.metadata?.representation_definitions
    ?? [];
  for (const definition of promptDefinitions) {
    names.push(definition?.name);
  }
  for (const block of payload.document?.blocks ?? []) {
    for (const representation of block.representations ?? []) {
      names.push(representation?.kind);
    }
  }
  return uniqueNonEmpty([...names, 'keywords', 'summary']);
}

function uniqueNonEmpty(values) {
  return [...new Set(values.map((value) => String(value || '').trim()).filter(Boolean))];
}

function upsertById(items, next) {
  return [
    ...items.filter((item) => item.id !== next.id),
    next,
  ].sort((first, second) => first.id.localeCompare(second.id));
}

async function fetchJson(url) {
  const response = await fetch(url);
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.detail || `Unable to load ${url}.`);
  }
  return payload;
}
