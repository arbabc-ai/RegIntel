# Warehouse Extraction Eval

- **Checks:** 5/5 matched a ground-truth value found directly in the raw text

| Query | Expected | Rows found | Match | Citation |
|---|---|---|---|---|
| common equity tier 1 | 4.5% | 3 | ✅ | cfr_title12_part217_Regulation-Q-Capital-Adequacy.txt::chunk-0226 |
| tier 1 capital ratio | 6% | 3 | ✅ | cfr_title12_part217_Regulation-Q-Capital-Adequacy.txt::chunk-0226 |
| total capital ratio | 8% | 3 | ✅ | cfr_title12_part217_Regulation-Q-Capital-Adequacy.txt::chunk-0018 |
| capital conservation buffer | 2.5% | 1 | ✅ | cfr_title12_part217_Regulation-Q-Capital-Adequacy.txt::chunk-0275 |
| liquidity coverage ratio | 1.0 ratio | 1 | ✅ | cfr_title12_part249_Regulation-WW-Liquidity-Coverage-Ratio.txt::chunk-0086 |
