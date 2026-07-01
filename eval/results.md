# RAG Eval Report

_Not yet run for the RegIntel (financial-regulatory) corpus._

Run the harness to generate this report:

```bash
python -m scripts.download_corpus   # fetch the regulatory corpus
python -m src.ingest                # chunk → embed → index
python -m eval.eval                 # writes this file
```

The harness reports **retrieval hit-rate@5**, **refusal accuracy** (the empty-`expected_sources`
cases in `questions.yaml`), and **mean Claude-as-judge faithfulness (0–3)**.
