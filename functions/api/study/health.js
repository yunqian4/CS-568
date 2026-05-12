export async function onRequestGet({ env }) {
  return new Response(
    JSON.stringify({
      ok: true,
      r2_configured: Boolean(env.STUDY_RESULTS),
    }),
    {
      headers: { 'Content-Type': 'application/json' },
    },
  );
}
