# Balanced v4: reliability and efficiency

New projects default to `workflow_mode: "balanced"`. Use `"strict"` for the
original v3 workflow. Existing v1-v3 workspaces are not migrated. Upstream domain
skills, original license restrictions and the strict scientific engine remain.

## Execution path

Plan once, then solve -> Host compute -> read-only scientific review per question,
then writing -> Host compilation/rendering -> read-only document review. Method
selection, small feasibility reasoning and fallback are inside solving, not
mandatory extra Agent stages. No mandatory separate conceptual diagram stage.

Workers have read/search/edit/write but no bash in balanced mode. They write
`code/qN/solve.py` and an independently implemented `validate.py`. The Host runs
these in the workspace with its own Python and finite budgets. Use deterministic
scripts (explicit random seeds, no network or background work). A successful
compute receipt binds code/helpers, inputs, dependencies, outputs and Python /
scientific package versions. The signed cache avoids repeated computations after
resume. Changed code/data/output sets invalidate the cache. Stale protocol outputs
are deleted before a rerun; an exit-zero no-op cannot reuse old results.

## Recovery and integrity

Prompt acknowledgement is not completion. A lease monitors ACK, activity, tools
and absolute Agent deadlines. Separate Host-operation budgets prevent a healthy
long calculation from being mistaken for an idle model or ordinary RPC transition.
Stage/task budgets and restart reservations survive recovery. A responsive event
loop, working filesystem and functioning operating system are assumptions.

Defaults: one restart per stage, three per task; up to three solver attempts, two
planning/writing attempts, and one review JSON correction per attempt. Repeated
identical failures advance to the simpler final strategy. `MATHMODEL_RT_*`
environment variables configure runtime policies. Deadlines are not extended by
noisy logs. Read-only reviewers cannot self-modify evidence. Incorrect results,
failed numeric checks, NaN, ambiguous JSON or unsupported claims are NOT auto-accepted.

Exhausted question failures remain unresolved. Only their dependents are blocked;
independent questions continue. Global runtime/credential/integrity faults stop
more model calls and preserve a truthful delivery report. Pausing cancels owned
processes before replay. Unconfirmed cleanup blocks resume rather than starting
another writer. Windows uses Job Objects assigned before a suspended process runs;
POSIX uses process groups and fences remaining members.

v4 Host checkpoints are HMAC signed. Exact input/accepted-evidence file sets are
frozen, including helper code, independent validation, figures and final paper.
Added files in a frozen tree are detected as well as modifications/deletions.
Signing is integrity detection, not a defense against an adversary who can access
the Host signing key. Host compute is NOT a hostile-code/filesystem sandbox;
external OS/container isolation is still required for untrusted code/multi-tenancy.

## Status and measurements

`completed`: all planned questions accepted and document review passed.
`completed_with_warnings`: same, with explicit reviewer/environment warnings.
`partial`: useful files retained, but NOT a fully accepted solution.
`failed`: integrity/system failure or no deliverables. `reports/DELIVERY.md`
lists accepted and missing work. Missing document tools can yield partial
mathematical results rather than preventing balanced mode from starting.

Browser fanout and transcript writing are bounded/coalesced, separate from RPC
reading. Streaming frontend messages use an index, not a full sort per delta.
Transcript persistence is eventual; shutdown attempts a bounded flush.
`/task/{id}/status` exposes runtime metrics, compute jobs, cache hits and delivery
status. Host prompts, observed assistant messages and provider retries are separate
counters. A prompt is NOT an exact model API call. Incomplete usage stays unknown.

## Validation and live benchmarks

`python -m pytest pi/tests -v` covers the legacy engine, new contracts, actual
numeric child processes, timeouts, cancellation, cache/receipt tampering, readonly
reviews, partial results and real TeX/Poppler rendering. Scripted JSONL peers test
complete RPC lifecycle, ACK-only hang recovery and Host-compute resume. These
are NOT real Pi/model accuracy benchmarks and do not establish contest quality.

```
python scripts/benchmark_pi.py pi/benchmarks/examples.json
python scripts/benchmark_pi.py pi/benchmarks/examples.json --execute \
  --base-url http://127.0.0.1:8000 --planner-model PROVIDER/MODEL \
  --worker-model PROVIDER/MODEL --mode balanced --out balanced-runs.jsonl
python scripts/summarize_pi_runs.py balanced-runs.jsonl
```

Use the actual local Bridge port. `--execute` is opt-in and may incur model costs;
keys remain in local Pi configuration, never in this repository or arguments.
Add historical contest inputs to the manifest. Keep models, inputs and budgets
fixed for repeated paired strict/balanced comparisons. Optional
`oracle: {"q1": {"objective": 12}}` checks selected v4 metrics. No oracle means
unknown quality, not a passing grade. No live model/contest results are claimed.

CI runs Python 3.11 on Linux and Windows, installs TeX/Poppler and a hash-verified
open Chinese font, and performs frontend unit tests, type checking and production
build. Linux legitimately skips Windows-only Job Object/locking tests; Windows
must execute them. Runtime and correctness acceptance must be evaluated separately.

## Concurrent paper-layout changes

Synced pi-integration through 3224ee832c4efce61bae1b8a728769cb059e9b14.
Balanced mode pins the selected CUMCM layout, copies it deterministically before
writing, and enforces the original source-layout checks before compilation.
Historical versioned layouts are not silently upgraded.
