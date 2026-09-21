import { ask } from "../_lib/rag.js";

const json = (body, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

export async function onRequestPost({ request, env }) {
  let payload;
  try {
    payload = await request.json();
  } catch {
    return json({ error: "invalid JSON" }, 400);
  }
  try {
    const { status, body } = await ask(env, payload?.question);
    return json(body, status);
  } catch (e) {
    // Never leak internals to the client; the platform log has the detail.
    console.error("ask failed", e);
    return json({ error: "service error" }, 502);
  }
}
