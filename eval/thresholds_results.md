# Warehouse Extraction Eval

- **Checks:** 4/5 matched a ground-truth value found directly in the raw text

| Query | Expected | Rows found | Match | Citation |
|---|---|---|---|---|
| common equity tier 1 | 4.5% | 2 | ✅ | cfr_title12_part217_Regulation-Q-Capital-Adequacy.txt::chunk-1125 |
| tier 1 capital ratio | 6% | 2 | ✅ | cfr_title12_part217_Regulation-Q-Capital-Adequacy.txt::chunk-1125 |
| total capital ratio | 8% | 1 | ✅ | cfr_title12_part217_Regulation-Q-Capital-Adequacy.txt::chunk-1125 |
| capital conservation buffer | 2.5% | 0 | ❌ | — |
| liquidity coverage ratio | 1.0 ratio | 1 | ✅ | cfr_title12_part249_Regulation-WW-Liquidity-Coverage-Ratio.txt::chunk-0086 |
