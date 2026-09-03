# MathModelAgent with Pi

This branch uses Pi as the Agent Skills harness. Claude Code is not required.

## What is integrated

- The upstream MathModelAgent skills and paper templates remain unchanged under `skills/`.
- `pi/skills/mathmodelagent-pi/SKILL.md` maps Claude-oriented wording to Pi tools and enforces stage boundaries.
- `pi/bridge.py` exposes the local HTTP/WebSocket API, manages one Pi RPC process per task, and advances fresh planning/problem/writing/verification sessions.
- `pi/staged_workflow.py` validates generated plans and candidates, builds bounded prompts, freezes accepted artifacts, and rejects writes outside the active stage.
- `pi/scientific_review.py` enforces schema-v2 scientific claims, strict Reviewer verdicts, controlled plan revisions, paper coverage, and manifest evidence.
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
2. Click **初始化项目**. The bridge preserves the folder layout under `workspaces/<project-id>/input/`, detects the main problem, and creates `project.json`, `input_manifest.json`, `todo.md`, `reports/`, `code/`, `results/`, `figures/`, and `paper/`.
3. Review the detected problem and data files, then choose competition, language, paper engine, and two Pi profiles. By default Sol high handles planning/review while Luna high handles execution/writing. Select **规划和执行使用同一模型** for the legacy single-model behavior.
4. Click **开始执行**. Only this step starts the Pi RPC process.

The bridge runs this contract-v2 sequence:

```text
Planning → Plan Audit
→ (Problem candidate → Scientific Review → accepted) × N
→ Paper Planning → Diagram → Writing → Document Verification
```

`execution_plan.json` records requested outputs, assumptions, claims, approximations, failure semantics, independent validation, dependencies, and runtime budgets. A fresh Sol session audits the plan before code starts. Luna workers may only submit candidates; a fresh Sol scientific Reviewer must accept each problem before the Host freezes it and starts a dependent problem.

Reviewer rejection is classified. Implementation/evidence issues return to Luna; method or ambiguity issues receive one controlled Sol replan for the current and unexecuted downstream problems. Contract-v2 projects never enter `waiting`: recoverable failures continue within fixed budgets, while exhausted budgets or indispensable missing input end as explicit `failed` without inventing results.

After all problems are scientifically accepted, Sol creates `paper_plan.json`, mapping every accepted claim to equations, algorithms, result evidence, independent validation, robustness, a non-empty applicability limitation, figures, and citation needs. Page range is advisory; missing scientific content is a hard failure. Luna writes the paper and `paper/paper_manifest.json`; manifest anchors must be unique, non-overlapping, and include a truthful limitation anchor for every claim. The Host also rejects explicit LaTeX bibliography entries that are unused or unresolved, duplicate contents sequences, consecutive forced page breaks, and short reference lists forced onto a separate page. Final Sol Document Verification checks coverage, frozen-number consistency, real and actually cited references, compilation, and every rendered PDF page. A failed document check can route through at most two bounded Luna paper repairs; each repair must render and inspect the cited failed pages before Sol re-verifies them.

Accepted code, results, figures, and reports are SHA-256 frozen. Schema-v2 figure evidence lives in each problem's `verification.json.figures`: it binds a scientific purpose and accepted claim to the generating script, real data paths, SciencePlots/API stack, vector master, PNG preview, language, and final-size checks. Ordinary plots use pinned SciencePlots, Seaborn/Matplotlib, and adjustText; specialized bundled figure templates may supply layout ideas only after replacing all simulated data. The Host rejects preview/example/`*_replica` evidence, raster-only plots, untracked generators/data, and cross-problem paths. Later stages cannot modify frozen artifacts, `input/`, the global plan, or future-problem directories. Long problem commands are aborted at the planned runtime limit and routed through the same repair policy.

Each started project keeps its isolated workspace and one persistent Pi RPC process while active, with fresh contexts at stage boundaries. The task page provides live chat, dynamic problem-level progress, review/repair attempts, tool output, workspace downloads, persistent pause/resume, cancellation, and final PDF preview. Closing or refreshing the browser does not cancel Pi.

**Pause and resume:** Pause first writes `status=paused`, the current stage/mode, timestamps, counters, plan version, and review state to `project.json`, then aborts the current agent/tool and terminates the full Pi process tree. Resume starts a new Pi RPC process with the correct frozen profile and reissues the prompt for the persisted mode without consuming an execution or review attempt. A bridge shutdown also persists active contract-v2 tasks as paused; an orphaned `running` task is normalized to paused when loaded after restart. Cancel remains terminal and is not resumable.

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
execution_plan.json
paper_plan.json
project.json
reports/PLAN_AUDIT.json
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
