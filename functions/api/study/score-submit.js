const ANSWER_PREFIX = 'study-private/answer-keys/';
const RESULT_PREFIX = 'study-results/';

export async function onRequestPost({ request, env }) {
  if (!env.STUDY_RESULTS) {
    return json({ detail: 'R2 binding STUDY_RESULTS is not configured.' }, 500);
  }

  let payload;
  try {
    payload = await request.json();
  } catch {
    return json({ detail: 'Invalid JSON payload.' }, 400);
  }

  const validation = validateSubmission(payload);
  if (validation) {
    return json({ detail: validation }, 400);
  }

  const questionSetId = payload.assignment.question_set_id;
  const answerObject = await env.STUDY_RESULTS.get(`${ANSWER_PREFIX}${questionSetId}.json`);
  if (!answerObject) {
    return json({ detail: 'Answer key not found.' }, 400);
  }

  const answerKey = await answerObject.json();
  const score = scoreAnswers(payload.answers, answerKey.answers);
  const now = new Date();
  const studyId = safeSegment(payload.study_id);
  const sessionId = safeSegment(payload.session_id || crypto.randomUUID());
  const resultKey = `${RESULT_PREFIX}${studyId}/${now.toISOString().slice(0, 10)}/${sessionId}.json`;

  await env.STUDY_RESULTS.put(
    resultKey,
    JSON.stringify(
      {
        ...payload,
        received_at: now.toISOString(),
        result_key: resultKey,
        score,
      },
      null,
      2,
    ),
    { httpMetadata: { contentType: 'application/json' } },
  );

  return json({ ok: true, session_id: sessionId });
}

export async function onRequestOptions() {
  return new Response(null, { headers: corsHeaders() });
}

function validateSubmission(payload) {
  if (!payload || typeof payload !== 'object') return 'Submission must be an object.';
  if (!payload.study_id) return 'Missing study_id.';
  if (!payload.participant_id) return 'Missing participant_id.';
  if (!payload.session_id) return 'Missing session_id.';
  if (!payload.assignment?.question_set_id) return 'Missing question_set_id.';
  if (!Array.isArray(payload.answers)) return 'Missing answers.';
  return '';
}

function scoreAnswers(answers, answerKey) {
  const keyById = new Map((answerKey || []).map((item) => [item.id, Number(item.answer_index)]));
  const answerById = new Map((answers || []).map((item) => [item.question_id, item]));
  let correct = 0;
  const details = [];
  for (const [questionId, correctIndex] of keyById.entries()) {
    const answer = answerById.get(questionId) || {};
    const rawSelectedIndex = answer.selected_index;
    const selectedIndex = rawSelectedIndex === null || rawSelectedIndex === undefined
      ? null
      : Number(rawSelectedIndex);
    const isCorrect = Number.isInteger(correctIndex) && selectedIndex === correctIndex;
    if (isCorrect) correct += 1;
    details.push({
      correct: isCorrect,
      question_id: questionId,
      selected_index: Number.isInteger(selectedIndex) ? selectedIndex : null,
    });
  }

  return {
    correct,
    details,
    total: keyById.size,
  };
}

function safeSegment(value) {
  return String(value || 'unknown').replace(/[^A-Za-z0-9_.-]+/g, '-').slice(0, 96) || 'unknown';
}

function json(payload, status = 200) {
  return new Response(JSON.stringify(payload), {
    headers: {
      ...corsHeaders(),
      'Content-Type': 'application/json',
    },
    status,
  });
}

function corsHeaders() {
  return {
    'Access-Control-Allow-Headers': 'Content-Type',
    'Access-Control-Allow-Methods': 'POST, OPTIONS',
    'Access-Control-Allow-Origin': '*',
  };
}
