# RegIntel hosted app (Cloudflare, $0 target)

Two-pane app: question on the left, cited answer + sources on the right. Runs on
Cloudflare Pages Functions with Workers AI (embeddings + generation) and Vectorize
(regulation chunks). No servers. Target host: `regintel.velodyn.com`.

```
browser ── POST /api/ask ──► Pages Function (functions/api/ask.js → _lib/rag.js)
                               ├─ Workers AI  bge-small-en-v1.5   (question → 384-dim vector)
                               ├─ Vectorize   regintel-fed         (top 6 chunks + text metadata)
                               ├─ refuse early if best score < MIN_SCORE   (no LLM call)
                               ├─ Workers AI  llama-3.1-8b-instruct (answer, cite as [n])
                               └─ citation post-check → answer, or refusal
```

Refusal is layered: low retrieval score, the model's own refusal, and a check that the
answer cites only source numbers it was actually given.

## Local checks (no Cloudflare access needed)
```bash
cd web && npm test                 # logic tests against stub bindings
node web/test/dev-server.mjs       # UI at http://localhost:8788, real ask() over stubs
```

## Going live (operator steps)
The existing Pages deploy token cannot use Workers AI or Vectorize (verified: 401/403).

1. **New API token** (dash.cloudflare.com → My Profile → API Tokens), permissions:
   Account · Workers AI · Edit, Account · Vectorize · Edit, Account · Cloudflare Pages · Edit.
   Save it outside the repo, e.g. `AI-OS/secrets/velodyn/cloudflare-regintel-token.txt`.
2. Create the index once (dims must match the embedding model):
   `npx wrangler vectorize create regintel-fed --dimensions=384 --metric=cosine`
3. Fetch + chunk every non-reserved Fed part (12 CFR Ch. II, ~70 parts) and load it:
   ```bash
   python -m scripts.fetch_fed_regs                 # data/fed/chunks.jsonl (gitignored)
   python -m scripts.upload_vectors --dry-run       # validate shape first
   CF_ACCOUNT_ID=... CF_API_TOKEN=... python -m scripts.upload_vectors
   ```
4. `cd web && npx wrangler pages project create regintel --production-branch=main`
   then `npx wrangler pages deploy public --project-name=regintel`.
5. Add the custom domain `regintel.velodyn.com` to the Pages project (Pages → Custom domains).

## Before this is public
- **Tune `MIN_SCORE`** in `functions/_lib/rag.js` (0.55 is a guess) using real questions,
  and run the existing eval questions against the deployed endpoint. An 8B open model is
  weaker than Claude; the evals decide whether it is good enough to show anyone.
- **Free-tier limits** (recall, unverified): Vectorize stored dimensions are capped, and
  Workers AI has a daily allowance. Check the current limits; the full corpus is roughly
  15–20k chunks. If over, trim the chunk count or move off the free plan.
- **Abuse/quota protection**: add Turnstile or per-IP rate limiting before a public link,
  or one visitor can exhaust the daily AI allowance.
- **Not legal advice** disclaimer is in the UI; a real customer offering needs proper terms.
- **State regulation** is deliberately not included: 50 different sources with different
  terms of use. Pilot 2–3 states after checking each source's terms.
