# JS Evolution Benchmark Corpus

This directory contains synthetic, locally authored fixtures for the v2.1
release benchmark. The runner reads these files directly; it does not start a
server, browser, model provider, or external network request.

The corpus covers:

- a stable configured DOM selector;
- class/attribute drift recovered by a configured fallback selector;
- an embedded JSON script whose selector and data path changed;
- a passively recorded XHR/fetch response whose endpoint and schema changed;
- delayed hydration recovered by a bounded wait-condition change;
- a non-empty but wrong selector candidate rejected by `QualityGate`;
- an irrecoverable negative that must remain failed.

The JSON response and hydration files are recordings made for this synthetic
lab, not traffic from a third-party target. `corpus.json` is the versioned
manifest and expected-output contract.
