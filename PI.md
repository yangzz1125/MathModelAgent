# MathModelAgent with Pi

This branch uses Pi as the Agent Skills harness. Claude Code is not required.

## What is integrated

- The upstream MathModelAgent skills and paper templates remain unchanged under `skills/`.
- `pi/skills/mathmodelagent-pi/SKILL.md` maps Claude-oriented wording to Pi tools and enforces stage boundaries.
- `pi/bridge.py` exposes the local HTTP/WebSocket API, manages one Pi RPC process per task, and advances fresh planning/problem/writing/verification sessions.
- `pi/tool_policy.ts` switches Pi's active tool capability set at each fresh stage: Reviewer sessions receive only `read/grep/find/ls`; maker/worker sessions receive `read/bash/edit/write`.
- `pi/scientific_review.py` enforces schema-v2 scientific claims, contract-v3 evidence levels, strict Reviewer verdicts, bounded local revisions, paper coverage, and manifest evidence.
- `scripts/start_web.ps1` starts the Pi bridge and the original Vue interface.
- The Vue task page shows Pi chat, workflow progress, tool calls, files, cancellation, and paper preview.
- `scripts/setup_pi.ps1` creates the isolated scientific Python environment.
- `scripts/start_pi.ps1` activates that environment and starts Pi in a separate contest workspace with all MathModelAgent skills loaded.
- `.pi/settings.json` makes the skills available when Pi is started from this repository.

The legacy FastAPI/Redis backend and the prebuilt desktop application are not used by this integration.

## Prerequisites

Required:

- Pi on `PATH`
- Python 3
- `numpy`, `pandas`, and `matplotlib`
- either `xelatex` or `typst`

Common modeling tasks also need `scipy`, `scikit-learn`, and `openpyxl`. `drawio` is optional.

Create or refresh the isolated scientific Python environment:

```powershell
powershell -ExecutionPolicy Bypass -File E:\MathModelAgentPi\scripts\setup_pi.ps1
```

The setup script installs `pi/requirements.txt` into `.venv-pi`. The launcher activates that environment only for the Pi process and does not modify system Python.

Check the environment from Pi with:

```text
/skill:doctor
```

## Web interface

The original Vue interface now talks to Pi through a thin local RPC bridge. It does not use the legacy Redis/multi-agent backend and does not expose API keys to the browser.

Start both services:

```powershell
powershell -ExecutionPolicy Bypass -File E:\MathModelAgentPi\scripts\start_web.ps1
```

To select a model explicitly:

```powershell
powershell -ExecutionPolicy Bypass -File E:\MathModelAgentPi\scripts\start_web.ps1 `
  -Model openai/gpt-5.6-sol `
  -Thinking high
```

Open `http://127.0.0.1:5173/chat`. The new-project flow is deterministic and does not call a model:

1. Choose an official contest folder such as `A题/` or select loose files.
2. Click **初始化项目**. The bridge preserves the folder layout under `workspaces/<project-id>/input/`, detects the main problem, and creates `project.json`, `input_manifest.json`, `todo.md`, `planning/`, `reports/`, `code/`, `results/`, `figures/`, and `paper/`.
3. Review the detected problem and data files, then choose competition, language, paper engine, and two Pi profiles. By default Sol high handles planning/review while Luna high handles execution/writing. Select **规划和执行使用同一模型** for the legacy single-model behavior.
4. Click **开始执行**. Only this step starts the Pi RPC process.

The bridge runs this contract-v3 sequence for new projects:

```text
Problem Inventory → Inventory Audit
→ (Method Card → Feasibility Spike → Method Audit
   → Problem candidate → Scientific Review → accepted) × N
→ Plan Completeness → Paper Planning → Diagram → Writing → Document Verification
```

Inventory declares stable requested-output IDs, exact inputs, dependencies, interpretations, and ambiguities without designing algorithms. Problems then proceed in dependency order. Host `artifact_missing` or schema validation errors on Inventory and Method Card artifacts receive at most two same-session, same-version local repairs; they do not consume semantic attempts or create superseded versions. Boundary/frozen-evidence violations remain terminal. Only a valid independent Reviewer rejection can create a new Inventory or Method version. Sol proposes and audits one Method Card; Luna runs a bounded representative Spike; only a strict Method Audit lets the Host append that problem to `execution_plan.json`. Luna then executes it and a fresh Sol scientific Reviewer must accept it before the Host freezes its artifacts. This interleaving lets downstream Spikes use accepted upstream code, evidence, and measured costs.

Each Method Card declares claims, evidence levels, finite domain, witness/bracket strategy, gap/tail exclusion, approximations, failure semantics, independent validation, figures, and an estimated cost model. `A_certified` means analytic/formal certification; `B_bounded_numerical` means a reproducible finite-domain numerical estimate with uncertainty/convergence and limitations; `C_exploratory` is supplementary and cannot cover a requested output. The Host computes a canonical executable-method hash. Claim-only revisions can reuse a matching Spike; executable changes must rerun it.

The main Spike budget is `max(20, min(120, floor(problem_runtime × 0.10)))` seconds. A Reviewer may request one specific supplemental Spike of at most 60 seconds. Spike code and measurements stay under the active version in `planning/`; they are planning evidence, never formal result or paper evidence. A timeout is a numerical/planning-feasibility failure, not mathematical infeasibility or a domain event. Host validation or budget errors receive at most two same-version local Spike repairs without consuming a Method revision; exhaustion fails the task, while artifact-boundary or frozen-evidence changes fail immediately.

Reviewer rejection is classified. Reviewer sessions are capability-restricted by the Pi extension to `read/grep/find/ls`; `bash`, `powershell`, `edit`, `write`, and extension tools are absent from the model tool list. The Bridge switches back to `read/bash/edit/write` only after starting a fresh maker/worker session. On Windows, contract-v3 additionally locks `project.json`, `planning/ledger.json`, and a dual-generation checksummed Host journal for the full Pi lifetime. Pi starts suspended, joins a `KILL_ON_JOB_CLOSE` Job Object, and resumes only after assignment; the Host releases control-state locks only after confirmed tree termination. Implementation/evidence issues after execution return to Luna. Method or ambiguity issues supersede only the current problem's provisional Method Card; downstream methods do not yet exist. A Method Card has an initial proposal plus at most two ordinary targeted revisions. Exhaustion ends as `failed`, except that a Reviewer may authorize one final A-to-B calibration when requested outputs are unchanged; Level C is never an automatic fallback. Contract-v3 never enters `waiting`.

The Host appends active Method Cards to a legacy-compatible schema-v2 `execution_plan.json`, composes `reports/ANALYSIS_MODELING_REPORT.md` from accepted versioned reports, and records immutable hashes in `planning/ledger.json`. After every problem passes Scientific Review, deterministic completeness checks require one Inventory entry, accepted Method Card, Method Audit, Scientific Review, and Level A/B coverage for every requested output. The resulting `reports/PLAN_COMPLETENESS.json` is checked again during Document Verification.

Historical contract-v1/v2 workspaces retain their original state machine and are not migrated or resumed as v3.

For contract-v3, the Host assembles a stage-specific workspace evidence allowlist before every prompt. Inventory may inspect all copied inputs; later Method, Spike, Execution, and read-only Review stages receive only the current problem's declared inputs, accepted dependency artifacts, and current Method/Spike or candidate lineage as applicable. Agents must not search Host implementation, tests, other workspaces, repository history, or unlisted superseded planning versions. Method Planning reads the figure catalog to choose a reference; Execution and Scientific Review receive only the selected catalog entries and previews. Independent reads should be batched in one turn.

After all problems are scientifically accepted, the Host gives Sol an explicit paper-evidence allowlist assembled from the problem statement, execution plan, completeness receipt, accepted reviews, and frozen artifacts. Paper Planning must not search `pi/`, tests, other workspaces, repository history, or superseded Method/Spike versions. Sol creates `paper_plan.json`, mapping every accepted claim to equations, algorithms, result evidence, independent validation, robustness, a non-empty applicability limitation, figures, and citation needs. Page range is advisory; missing scientific content is a hard failure. Luna receives the same allowlist plus the paper plan and current Diagram artifacts, reads the writing skill for template rules, and uses shell commands only to compile/render/validate the paper. It writes the paper and `paper/paper_manifest.json`; manifest anchors must be unique, non-overlapping, and include a truthful limitation anchor for every claim. The Host also rejects explicit LaTeX bibliography entries that are unused or unresolved, duplicate contents sequences, consecutive forced page breaks, and short reference lists forced onto a separate page. Final Sol Document Verification receives an explicit list of paper source, manifest, existing log/PDF, rendered pages, and accepted evidence; it is capability-restricted to `read/grep/find/ls` and returns strict JSON without compiling, rendering, or writing files. The Host rechecks the frozen chain and PDF, writes `reports/VERIFY_REPORT.md`, and alone marks completion. One malformed Reviewer JSON response receives a read-only protocol retry without invoking Luna; a valid rejection can route through at most two bounded Luna paper repairs.

Accepted code, results, figures, and reports are SHA-256 frozen. Schema-v2 figure evidence lives in each problem's `verification.json.figures`: it binds a scientific purpose and accepted claim to the generating script, real data paths, SciencePlots/API stack, vector master, PNG preview, language, and final-size checks. Ordinary plots use pinned SciencePlots, Seaborn/Matplotlib, and adjustText; specialized bundled figure templates may supply layout ideas only after replacing all simulated data. The Host rejects preview/example/`*_replica` evidence, raster-only plots, untracked generators/data, and cross-problem paths. Later stages cannot modify frozen artifacts, `input/`, the global plan, or future-problem directories. Long problem commands are aborted at the planned runtime limit and routed through the same repair policy.

Each started project keeps its isolated workspace and one persistent Pi RPC process while active, with fresh contexts at stage boundaries. The task page provides live chat, dynamic problem-level progress, review/repair attempts, tool output, workspace downloads, persistent pause/resume, cancellation, and final PDF preview. Closing or refreshing the browser does not cancel Pi.

**Pause and resume:** Pause first writes `status=paused`, the current stage/mode, timestamps, counters, plan version, ledger version, Spike elapsed budget, and review state to disk, then aborts the current agent/tool and terminates the full Pi process tree. Resume starts a new Pi RPC process with the correct frozen profile and reissues the prompt for the persisted mode without consuming an execution, proposal, Spike, or review attempt. A bridge shutdown also persists active contract-v2/v3 tasks as paused; an orphaned `running` task is normalized to paused when loaded after restart. Cancel remains terminal and is not resumable.

## Interactive TUI use

Create a workspace outside this repository and start Pi:

```powershell
powershell -ExecutionPolicy Bypass -File E:\MathModelAgentPi\scripts\start_pi.ps1 `
  -Workspace E:\MathModelProjects\my-contest-problem
```

Then run:

```text
/skill:mathmodelagent-pi
```

For direct TUI use, state one stage explicitly. The compatibility skill no longer chains all stages by itself because the web bridge owns autonomous sequencing. Example:

```text
Planning stage only: use LaTeX, MCM/ICM format, and English. Read the problem files, run 2analysis-modeling, write the analysis report and execution_plan.json, then stop.
```

## Non-interactive use

For one prompt and one Pi session:

```powershell
powershell -ExecutionPolicy Bypass -File E:\MathModelAgentPi\scripts\start_pi.ps1 `
  -Workspace E:\MathModelProjects\my-contest-problem `
  -Print `
  -Prompt "Use LaTeX and English MCM format. Run the complete MathModelAgent workflow on the problem files in this workspace."
```

Use `-Model provider/model-id` only when overriding the model already configured in Pi.

## Output

The staged web workflow writes only to the selected contest workspace:

```text
planning/ledger.json
planning/inventory/v<version>/problem_inventory.json
planning/inventory/v<version>/audit.json
planning/methods/<problem-id>/v<version>/method_card.json
planning/methods/<problem-id>/v<version>/spike/
planning/methods/<problem-id>/v<version>/audit_<attempt>.json
execution_plan.json
paper_plan.json
project.json
reports/PLAN_COMPLETENESS.json
reports/<problem-id>_SCIENTIFIC_REVIEW.json
reports/PAPER_PLAN.md
code/<problem-id>/
results/<problem-id>/result.json       # status: candidate
results/<problem-id>/verification.json # claim evidence
figures/<problem-id>/
paper/paper_manifest.json
paper/
```

Initialization creates directories and deterministic manifests only; it does not call a model or create empty result/paper files. Stage handoffs use files rather than prior chat context. A successful run ends with a renderable non-empty PDF and an explicit `PASS` conclusion in `reports/VERIFY_REPORT.md`.

## Upstream updates

Keep the Pi integration branch separate from upstream `main`:

```powershell
git fetch origin
git rebase origin/main
```

Resolve changes only in `pi/`, `.pi/settings.json`, `scripts/start_pi.ps1`, and this document. Avoid modifying upstream skills solely for Pi compatibility.

## License

MathModelAgent permits personal use but prohibits commercial use, closed-source distribution, and commercial services. See `docs/md/License.md`.
