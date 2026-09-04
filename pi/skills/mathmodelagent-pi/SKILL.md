---
name: mathmodelagent-pi
description: Pi entry point for the complete MathModelAgent competition workflow. Use when the user wants to analyze a modeling problem, implement and run models, generate figures, write a Typst or LaTeX paper, and validate the final PDF without Claude Code.
compatibility: Requires Pi, Python, and either xelatex or typst. Optional drawio improves conceptual diagrams.
---

# MathModelAgent for Pi

Run the upstream MathModelAgent skills with Pi as the harness. Do not invoke Claude Code, read `.claude/settings.json`, or depend on Claude-specific hooks.

## Tool mapping

Interpret upstream tool names as follows:

- `Read` -> Pi `read`
- `Bash` -> Pi `bash`
- `Write` -> Pi `write`
- `Edit` -> Pi `edit`
- `Grep` and `Glob` -> `rg` and `rg --files` through Pi `bash`
- `AskUserQuestions` -> ask a concise question in normal chat
- `Agent` -> use Pi subagents only when available and genuinely useful; otherwise execute the stage in the main agent

The upstream `allowed-tools` frontmatter is descriptive. Actual authority comes from the tools enabled in the current Pi session.

## Root and workspace

`MATHMODELAGENT_ROOT` points to the separately cloned MathModelAgent repository. The current working directory is the modeling project workspace. Write all generated work to the current workspace; never write generated contest artifacts into `MATHMODELAGENT_ROOT`.

If `MATHMODELAGENT_ROOT` is unavailable, infer it from the loaded skill path. Stop with a clear error if these files cannot be found:

```text
skills/1start-mathmodel/SKILL.md
skills/2analysis-modeling/SKILL.md
skills/3coding-visual/SKILL.md
skills/4drawio/SKILL.md
skills/5writing/SKILL.md
skills/6verity/SKILL.md
```

## Preferences

Resolve these once before execution:

- paper engine: LaTeX or Typst;
- competition/template family;
- paper language;
- known subproblem count, or infer it from the statement.

Use preferences already present in the user's initial request. Ask only for missing choices that materially affect output. Record them in `plan.md`. Once recorded, do not ask for the same preference again when `5writing` is loaded.

Default to LaTeX when `xelatex` is available and no engine is specified. MCM/ICM/COMAP papers default to English.

## Stage authority

The Pi bridge owns workflow sequencing. Execute only the stage named in the current prompt:

- inventory reads the statement and writes only the versioned problem inventory; it does not design methods;
- inventory and method auditors are read-only and return only the requested strict JSON;
- a method maker writes one versioned Method Card for the named problem only;
- a feasibility Spike writes only inside that Method Card version's `spike/` directory and never writes formal results;
- a problem worker reads `skills/3coding-visual/SKILL.md` plus `pi/skills/mathmodel-figure-quality/SKILL.md`, uses the pinned publication plotting stack, and stops after the named problem;
- diagram, writing, and verification read only their corresponding upstream skill.

Never continue into another stage on your own. A fresh Pi session may begin each stage, so use workspace files as the only handoff. Treat `input/`, `execution_plan.json`, `planning/ledger.json`, prior planning versions, the global analysis report, and completed problem directories as read-only unless the bridge prompt explicitly names a new versioned write path.

## Scientific authority

Planning and execution agents produce proposals, not acceptance decisions. A worker must label result and verification artifacts `candidate`; only the bridge may mark a problem accepted after a fresh scientific Reviewer returns a strict all-pass verdict. Never describe your own candidate as accepted or final.

Reviewer prompts are read-only. The Bridge tool-policy extension removes `bash`, `powershell`, `edit`, `write`, and extension tools from every Reviewer session; only `read`, `grep`, `find`, and `ls` remain active. Re-read the original statement and generic modeling norms, distrust prior summaries and self-reported checks, and reject undeclared approximations, conflated failure semantics, unsupported optimality/event claims, or validation equivalent to the primary method. Return only the strict JSON requested by the bridge.

Scientific acceptance happens per problem before artifacts are frozen. For contract-v3, use only the Host-assembled stage allowlist: Inventory may inspect all copied inputs, while Method, Spike, Execution, and Review stages receive the current problem's declared inputs, accepted dependency artifacts, and current Method/Spike or candidate lineage as applicable. Method Planning may read the full figure catalog to select a reference; Execution and Scientific Review receive only the selected catalog entries and previews. Do not inspect Host `pi/*.py`, tests, other workspaces, repository history, or unlisted superseded Method/Spike versions to rediscover the contract. Batch independent reads in one turn and report missing evidence rather than searching outside the list. Paper Planning and Writing additionally receive the completeness receipt, accepted reviews, frozen artifacts, paper plan, and current Diagram outputs as applicable; use shell commands during Writing only for paper compilation, rendering, and validation. Document verification later receives exact paper source, manifest, log/PDF, rendered-page, and evidence paths and checks content coverage, numerical consistency, compilation, references, and PDF quality; it does not retroactively replace scientific review. Every strict-JSON Reviewer receives at most one same-session protocol correction; this does not consume audit/scientific attempts, trigger Producer repair, or create semantic versions, and the budget survives pause/resume. A second malformed response fails with `review_protocol`. The Document Reviewer remains read-only; the Host writes `reports/VERIFY_REPORT.md` and decides completion. New contract-v3 projects do not wait for manual repair: stay within bounded automatic repair/replan instructions, or fail clearly when evidence or indispensable input cannot be recovered. Historical contract-v1/v2 workspaces keep their original state machine and are never migrated in place.

Contract-v3 uses `Problem Inventory → per-problem Method Card → Feasibility Spike → Method Audit → Candidate → Scientific Review`. Sol high owns makers/auditors for inventory and methods; Luna high owns Spikes and execution. Host `artifact_missing` or schema validation errors on Inventory and Method Card output receive at most two same-session, same-version local repairs without changing semantic attempts; boundary/frozen-evidence violations fail immediately. Only a valid independent Reviewer rejection creates a new Inventory or Method version. A Method Audit accept is provisional until the same problem passes Scientific Review. Method/ambiguity rejection supersedes only the current version because downstream methods have not yet been planned.

Evidence levels are binding. `A_certified` promises analytic/formal certification. `B_bounded_numerical` promises a finite domain, reproducible resolution, uncertainty/convergence checks, and explicit limitations. `C_exploratory` is supplementary and cannot satisfy a requested output. Do not move the proof target during review. After ordinary Method Audit attempts are exhausted, only a Host-authorized, Reviewer-listed A-to-B calibration may receive one final revision; never downgrade automatically to Level C.

A Spike is planning evidence only. Benchmark representative kernels and obtain requested witnesses/brackets within the Host budget; do not run the full solution or cite Spike artifacts as final scientific evidence. Timeout means computational feasibility is unproven or a numerical process failed. It never proves mathematical infeasibility and never constitutes a domain event. Host validation or budget errors receive at most two same-version local repairs without consuming a Method revision; repair exhaustion fails, and boundary/frozen-artifact violations remain terminal.

Run generated model code before reporting a problem complete. Use actual result files and figures as the only source for numerical claims. Every schema-v2 `verification.json` includes `figures`; each generated figure must declare its accepted-claim purpose, generator, real data paths, SciencePlots/API stack, vector master, PNG preview, language, and completed visual checks. Never use bundled template simulation, preview images, examples, or `*_replica` outputs as paper evidence. Ordinary charts use the pinned SciencePlots + Seaborn/Matplotlib stack; existing complex templates are layout references only when the Planner declared their scientific purpose. During Document Verification, return only the requested strict JSON; never write the verification report or claim Host acceptance. Final success exists only after the Host-generated `reports/VERIFY_REPORT.md` says `PASS` and the compiled PDF is readable.

For problem stages, benchmark one representative computation before a long loop. Keep search and acceptance predicates identical, distinguish numerical errors from mathematical infeasibility, and stop when the current problem artifacts are complete.

If optional Draw.io is unavailable, omit nonessential conceptual diagrams and record that limitation. Do not block numerical modeling, paper generation, or PDF validation solely because Draw.io is missing.
