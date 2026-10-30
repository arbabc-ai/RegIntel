"""RegIntel Phase 1 provision parsing, relational index and retrieval evaluation (standard library only).

Everything here treats the acquired corpus as untrusted evidence: parsers never execute content, every
provision keeps its source hash, locator and character offsets, and no function decides whether a rule
applies to a bank.
"""
