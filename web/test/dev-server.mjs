// Local UI preview: serves web/public and runs the REAL ask() against stub AI/Vectorize
// bindings, so the two-pane UI can be exercised with no Cloudflare access.
// Run: node web/test/dev-server.mjs   ->  http://localhost:8788
import http from "node:http";
import { readFile } from "node:fs/promises";
import { ask } from "../functions/_lib/rag.js";

const PASSAGE = {
  source: "12 CFR Part 217", title: "Capital Adequacy of Bank Holding Companies (Regulation Q)",
  text: "A Board-regulated institution must maintain a common equity tier 1 capital ratio of at least 4.5 percent.",
};
const env = {
  AI: {
    async run(model, input) {
      if (model.includes("bge")) return { data: [[0.1, 0.2]] };
      return { response: "The minimum common equity tier 1 capital ratio is 4.5 percent [1]." };
    },
  },
  VECTORIZE: {
    async query() {
      const off = process.env.STUB_OFFTOPIC === "1";
      return { matches: [{ id: "x", score: off ? 0.2 : 0.83, metadata: PASSAGE }] };
    },
  },
};

http.createServer(async (req, res) => {
  if (req.method === "POST" && req.url === "/api/ask") {
    let raw = "";
    for await (const c of req) raw += c;
    const { status, body } = await ask(env, JSON.parse(raw).question);
    res.writeHead(status, { "content-type": "application/json" });
    return res.end(JSON.stringify(body));
  }
  res.writeHead(200, { "content-type": "text/html" });
  res.end(await readFile(new URL("../public/index.html", import.meta.url)));
}).listen(8788, () => console.log("http://localhost:8788"));
