# Validation Without Model Weights

Software checks, numerical consistency, agent task completion and scientific
model validity are different claims. Passing one layer does not imply the next.
Specialized model weights and emotion-recognition accuracy are outside the
current validation scope.

## Deterministic Workflow Acceptance

From the repository root, with the worker dependencies installed:

```bash
python -m evals.acceptance --input data/record.bdf --out storage/validation/run-001
```

For a headerless array, supply independently confirmed metadata:

```bash
python -m evals.acceptance --input data/record.npy --sample-rate 128 --channels F3,F4 --out storage/validation/run-002
```

The output directory must be empty and separate from the input. Existing
results are never overwritten. The protocol:

- Hashes the input record and its known companion files before and after.
- Registers the input and observes at most the first 20 seconds.
- Creates non-overlapping two-second windows without padding or crossing
  discontinuities. Shorter segments cannot satisfy this protocol.
- Extracts standard-band power, then independently recomputes Welch PSD and
  trapezoidal integration using SciPy, not the worker feature helpers.
- Checks feature arrays, summaries, exported CSV values/units, readable SVG,
  non-empty artifact files and source/parent lineage.
- Saves the checks, input digest, metadata evidence, observed range, numerical
  error and artifact IDs in `acceptance.json`.

The reference shares the project's input reader. It checks calculations after
decoding, not independent correctness of every file decoder or physical
calibration. Inferred units remain inferred. It does not verify an LLM's
planning, clinical interpretation or emotion labels.

The fixed array tolerance is `rtol=1e-5, atol=1e-6`. Rounded absolute summaries
use `rtol=1e-3, atol=1e-4`; relative summaries use `atol=5.1e-5`. Tolerances
reflect floating-point computation and the published summary rounding, not
model prediction error. Shape, lineage and file checks are exact.

The analytical regression fixture has a 10 Hz sine of amplitude 10 in one
channel and a 6 Hz sine of amplitude 5 in another. Mean alpha-band power across
the two channels is 25 in squared input units, with four two-second windows.
This fixture has a numerical reference, not emotion ground truth.

## Source Location And Retrieval

```bash
python -m eegagent.knowledge audit --strict
python -m eegagent.knowledge eval evals/knowledge_qa.yaml -k 5
python -m eegagent.knowledge eval evals/knowledge_evidence.yaml -k 5
python -m eegagent.knowledge eval evals/knowledge_backfill.yaml -k 5
```

`audit` is offline: it checks explicit document IDs, sections, source types and
quote anchors. Missing parsed documents or quotes remain unresolved. Availability
and hashes of original binary files are checked separately for download links. Retrieval eval
may use paid embedding/reranking APIs if configured; direct Python commands use
`EEGAGENT_*`, whereas launchers map the `EFAGENT_*` aliases.

Document hit rate and delivery of the expected original excerpt are reported
separately. Neither proves that every claim is supported or that an LLM used a
citation correctly. These are development regression sets, not a held-out
scientific benchmark.

Original papers and private upstream materials are not all distributed in the
repository. Import permitted copies on a new machine. A missing or
hash-mismatched original has no original-download link; the saved parsed excerpt
remains available and is labelled accordingly.

## Live Agent Acceptance

Start an isolated instance with a new storage directory, a configured chat
model and no specialized model weights. Then use fresh output directories:

```bash
python -m evals.run --gateway http://127.0.0.1:18080 --only L1-01 --runs 1 --out storage/validation/agent-plot-001
python -m evals.run --gateway http://127.0.0.1:18080 --only L2-01 --runs 1 --out storage/validation/agent-numeric-001
```

These use the declared synthetic fixtures. A tool name appearing in a trace is
not a success result. Inspect final files, errors, prohibited calls and the
independent result checks; tasks with no final-result check remain unscored for
`task_success`. A failed attempt is retained, not retried until it passes and
then silently substituted for the original.

Use `python -m evals.run --report PATH/results.jsonl --out NEW_REPORT_DIRECTORY`
to render the captured results without another model call. Do not compare
different chat models, prompts, datasets or task budgets as one run. This
protocol does not establish real emotion accuracy, which requires independent
labels, frozen predictions and an appropriate subject/session split.

## Portability And Security

The CI workflow defines separate Go and Python checks on Linux, Windows and
macOS, plus a frontend build/test and published-asset consistency check. A
workflow being configured is not evidence that every platform has passed;
check the actual CI run. CI uses the current stable Go toolchain; the `1.22`
in `go.mod` is a language-version floor, not a recommended toolchain.

The application remains local-only. Host/Origin guards reduce browser
cross-origin and DNS-rebinding risks; stripped child environment variables do
not constitute OS/container isolation. Do not expose the worker or gateway on
a public interface.
