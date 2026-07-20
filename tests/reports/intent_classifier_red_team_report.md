# Intent Classifier Red-Team Report

## Purpose

This report records direct, synthetic evaluation of the intent-classifier MCP.
It measures probabilistic routing quality; it does not authorize access or
replace the deterministic policy gate. The corpus and generated reports must
never contain PHI, production prompts, or credentials.

## How To Run

Start only the intent MCP server, then run one pass or three repeated passes:

```powershell
uv run --no-editable agent-harness-intent
uv run --no-editable agent-harness-eval --suite intent
uv run --no-editable agent-harness-eval --suite intent --repetitions 3
```

The command writes `evals/results/intent-evaluation-<timestamp>.json`. Label
differences are findings and do not make the command fail; an unavailable MCP
server or malformed MCP response is an operational failure and exits non-zero.

## Rounds

| Round | Focus | Cases |
| --- | --- | ---: |
| 1 | Clear routing baseline: safe metadata, ambiguity, patient requests, policy probes, and SQL | 30 |
| 2 | Boundary attacks: classifier injection, disguised SQL, mixed requests, ambiguity, and safe paraphrases | 30 |
| 3 | Robustness and usability: obfuscation, near-neighbor prompts, false-positive controls, and tool-instruction attacks | 30 |

After each run, record the model identifier, prompt version, repetitions, key
metrics, and every meaningful miss. Preserve misses in `evals/intent.jsonl` as
regressions; do not remove them to improve a score.

## Metrics To Review

- Per-intent and recommended-action accuracy
- Clarification accuracy for underspecified requests
- Unsafe-to-safe routes for `must_refuse` cases
- Safe requests unnecessarily clarified or refused
- Confidence range and confusion matrix

A low score is a discovery result. The hard deterministic checks are the
classifier's structured-output and MCP-contract tests; live results describe
model behavior and variability.
