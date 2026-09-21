import test from "node:test";
import assert from "node:assert/strict";
import { ask, checkCitations, validateQuestion, REFUSAL, MIN_SCORE } from "../functions/_lib/rag.js";

const match = (id, score, text = "Passage text.") =>
  ({ id, score, metadata: { source: "12 CFR Part 217", title: "Regulation Q", text } });

function env({ matches, answer }) {
  const calls = { gen: 0 };
  return {
    calls,
    AI: {
      async run(model, input) {
        if (model.includes("bge")) return { data: [[0.1, 0.2]] };
        calls.gen++;
        return { response: answer };
      },
    },
    VECTORIZE: { async query() { return { matches }; } },
  };
}

test("validateQuestion rejects short, long and non-string input", () => {
  assert.ok(validateQuestion("hi"));
  assert.ok(validateQuestion("x".repeat(501)));
  assert.ok(validateQuestion(42));
  assert.equal(validateQuestion("What is the CET1 minimum?"), null);
});

test("checkCitations requires at least one valid citation", () => {
  const src = [{ n: 1 }, { n: 2 }];
  assert.equal(checkCitations("No citations here.", src).ok, false);
  assert.equal(checkCitations("Claim [3].", src).ok, false); // never supplied
  assert.deepEqual(checkCitations("A [2] and B [1] and again [2].", src), { ok: true, cited: [1, 2] });
});

test("grounded answer passes through with sources", async () => {
  const e = env({ matches: [match("a", 0.8), match("b", 0.7)], answer: "The minimum is 4.5% [1]." });
  const { status, body } = await ask(e, "What is the CET1 minimum?");
  assert.equal(status, 200);
  assert.equal(body.refused, false);
  assert.deepEqual(body.cited, [1]);
  assert.equal(body.sources.length, 2);
});

test("low retrieval score refuses WITHOUT calling the LLM", async () => {
  const e = env({ matches: [match("a", MIN_SCORE - 0.1)], answer: "should never be used [1]" });
  const { body } = await ask(e, "What is the airspeed of a swallow?");
  assert.equal(body.refused, true);
  assert.equal(body.reason, "no_relevant_sources");
  assert.equal(e.calls.gen, 0);
});

test("uncited model answer is downgraded to a refusal", async () => {
  const e = env({ matches: [match("a", 0.9)], answer: "The minimum is 4.5%." });
  const { body } = await ask(e, "What is the CET1 minimum?");
  assert.equal(body.refused, true);
  assert.equal(body.reason, "failed_citation_check");
  assert.equal(body.answer, REFUSAL);
});

test("citation to a source number that was never supplied is refused", async () => {
  const e = env({ matches: [match("a", 0.9)], answer: "The minimum is 4.5% [7]." });
  const { body } = await ask(e, "What is the CET1 minimum?");
  assert.equal(body.reason, "failed_citation_check");
});

test("model's own refusal is surfaced as a refusal", async () => {
  const e = env({ matches: [match("a", 0.9)], answer: REFUSAL });
  const { body } = await ask(e, "What is the CET1 minimum?");
  assert.equal(body.refused, true);
  assert.equal(body.reason, "model_refused");
});

test("bad question returns 400 before any binding is called", async () => {
  const e = env({ matches: [], answer: "" });
  const { status } = await ask(e, "no");
  assert.equal(status, 400);
});
