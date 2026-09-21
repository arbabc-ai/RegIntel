// Pure RAG helpers for the hosted RegIntel app. No Cloudflare bindings in here,
// so everything is unit-testable under plain `node --test`.

export const EMBED_MODEL = "@cf/baai/bge-small-en-v1.5"; // 384-dim: fits the free Vectorize dimension budget
export const GEN_MODEL = "@cf/meta/llama-3.1-8b-instruct";
export const TOP_K = 6;
// Below this best-match cosine score we refuse without calling the LLM at all: cheaper,
// and an ungrounded answer is worse than none in this domain. TUNE against the evals.
export const MIN_SCORE = 0.55;
export const MAX_QUESTION_CHARS = 500;

export const SYSTEM_PROMPT = `You answer questions about U.S. Federal Reserve regulations (12 CFR Chapter II) using ONLY the numbered sources provided.
Rules:
- After every claim, cite the supporting source(s) as [1], [2], etc.
- If the sources do not contain enough information to answer, reply exactly: "The provided regulations do not contain enough information to answer this."
- Never use outside knowledge. Never give legal advice. Be concise.`;

export const REFUSAL = "The provided regulations do not contain enough information to answer this.";

export function validateQuestion(q) {
  if (typeof q !== "string") return "question must be a string";
  const t = q.trim();
  if (t.length < 5) return "question is too short";
  if (t.length > MAX_QUESTION_CHARS) return `question is over ${MAX_QUESTION_CHARS} characters`;
  return null;
}

// Vectorize matches -> the source list shown to both the model and the user.
export function toSources(matches) {
  return matches.map((m, i) => ({
    n: i + 1,
    id: m.id,
    score: m.score,
    source: m.metadata?.source ?? "",
    title: m.metadata?.title ?? "",
    text: m.metadata?.text ?? "",
  }));
}

export function shouldRefuseBeforeLLM(sources) {
  return sources.length === 0 || sources[0].score < MIN_SCORE;
}

export function buildUserPrompt(question, sources) {
  const ctx = sources.map((s) => `[${s.n}] (${s.source} — ${s.title})\n${s.text}`).join("\n\n");
  return `Sources:\n${ctx}\n\nQuestion: ${question.trim()}`;
}

// Citation post-check (the "regex enforcement" step from docs/architecture.md): an answer
// that cites nothing, or cites a source number we never supplied, is treated as ungrounded.
export function checkCitations(answer, sources) {
  const valid = new Set(sources.map((s) => s.n));
  const cited = [...answer.matchAll(/\[(\d+)\]/g)].map((m) => Number(m[1]));
  if (cited.length === 0) return { ok: false, cited: [] };
  if (cited.some((n) => !valid.has(n))) return { ok: false, cited };
  return { ok: true, cited: [...new Set(cited)].sort((a, b) => a - b) };
}

export async function ask(env, question) {
  const bad = validateQuestion(question);
  if (bad) return { status: 400, body: { error: bad } };

  const emb = await env.AI.run(EMBED_MODEL, { text: [question.trim()] });
  const vector = emb?.data?.[0];
  if (!vector) return { status: 502, body: { error: "embedding failed" } };

  const res = await env.VECTORIZE.query(vector, { topK: TOP_K, returnMetadata: "all" });
  const sources = toSources(res?.matches ?? []);

  if (shouldRefuseBeforeLLM(sources)) {
    return { status: 200, body: { answer: REFUSAL, refused: true, reason: "no_relevant_sources", sources: [] } };
  }

  const out = await env.AI.run(GEN_MODEL, {
    messages: [
      { role: "system", content: SYSTEM_PROMPT },
      { role: "user", content: buildUserPrompt(question, sources) },
    ],
    max_tokens: 600,
    temperature: 0,
  });
  const answer = (out?.response ?? "").trim();
  if (!answer) return { status: 502, body: { error: "generation failed" } };

  if (answer.includes(REFUSAL)) {
    return { status: 200, body: { answer: REFUSAL, refused: true, reason: "model_refused", sources } };
  }
  const check = checkCitations(answer, sources);
  if (!check.ok) {
    return { status: 200, body: { answer: REFUSAL, refused: true, reason: "failed_citation_check", sources } };
  }
  return { status: 200, body: { answer, refused: false, cited: check.cited, sources } };
}
