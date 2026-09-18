# RAG Eval Report

- **Generation + judge model:** `ollama:qwen2.5:7b-instruct-q4_K_M`
- **Embeddings:** `ollama:nomic-embed-text`
- **Total questions:** 11 (8 retrieval, 3 refusal)
- **Retrieval hit-rate@5:** 8/8 = 100%
- **Refusal accuracy:** 3/3 = 100%
- **Mean faithfulness (0-3):** 2.00

| # | Type | Hit | Faith | Question | Retrieved | Expected |
|---|---|---|---|---|---|---|
| 1 | retrieval | ✅ | 3 | What minimum liquidity coverage ratio must a covered institu | cfr_title12_part249_Regulation-WW-Liquidity-Covera | cfr_title12_part249_Regulation-WW-Liquid |
| 2 | retrieval | ✅ | 2 | What qualifies as a high-quality liquid asset (HQLA) under t | cfr_title12_part249_Regulation-WW-Liquidity-Covera | cfr_title12_part249_Regulation-WW-Liquid |
| 3 | retrieval | ✅ | 1 | What is the minimum common equity tier 1 (CET1) capital rati | cfr_title12_part217_Regulation-Q-Capital-Adequacy. | cfr_title12_part217_Regulation-Q-Capital |
| 4 | retrieval | ✅ | 3 | What is the capital conservation buffer? | cfr_title12_part217_Regulation-Q-Capital-Adequacy. | cfr_title12_part217_Regulation-Q-Capital |
| 5 | retrieval | ✅ | 2 | What minimum total capital ratio must a banking organization | cfr_title12_part217_Regulation-Q-Capital-Adequacy. | cfr_title12_part217_Regulation-Q-Capital |
| 6 | retrieval | ✅ | 3 | What does Regulation YY require for company-run stress testi | cfr_title12_part252_Regulation-YY-Enhanced-Prudent | cfr_title12_part252_Regulation-YY-Enhanc |
| 7 | retrieval | ✅ | 3 | When must a bank file a Suspicious Activity Report? | cfr_title31_part1020_BSA-AML-Rules-for-Banks.txt | cfr_title31_part1020_BSA-AML-Rules-for-B |
| 8 | retrieval | ✅ | 3 | What customer identification program requirements apply to b | cfr_title31_part1020_BSA-AML-Rules-for-Banks.txt | cfr_title31_part1020_BSA-AML-Rules-for-B |
| 9 | refusal | ✅ | 2 | What is the Volcker Rule's de minimis threshold for propriet | cfr_title12_part217_Regulation-Q-Capital-Adequacy. | (refuse) |
| 10 | refusal | ✅ | 0 | What are the state-level money transmitter licensing rules i | cfr_title12_part252_Regulation-YY-Enhanced-Prudent | (refuse) |
| 11 | refusal | ✅ | 0 | How do I calculate my personal income tax? | cfr_title12_part217_Regulation-Q-Capital-Adequacy. | (refuse) |
