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

- planning reads `skills/2analysis-modeling/SKILL.md` and stops after planning artifacts;
- a problem worker reads `skills/3coding-visual/SKILL.md` plus `pi/skills/mathmodel-figure-quality/SKILL.md`, uses the pinned publication plotting stack, and stops after the named problem;
- diagram, writing, and verification read only their corresponding upstream skill.

Never continue into another stage on your own. A fresh Pi session may begin each stage, so use workspace files as the only handoff. Treat `input/`, `execution_plan.json`, the global analysis report, and completed problem directories as read-only unless the bridge prompt explicitly says otherwise.

## Scientific authority

Planning and execution agents produce proposals, not acceptance decisions. A worker must label result and verification artifacts `candidate`; only the bridge may mark a problem accepted after a fresh scientific Reviewer returns a strict all-pass verdict. Never describe your own candidate as accepted or final.

Reviewer prompts are read-only. Re-read the original statement and generic modeling norms, distrust prior summaries and self-reported checks, and reject undeclared approximations, conflated failure semantics, unsupported optimality/event claims, or validation equivalent to the primary method. Return only the strict JSON requested by the bridge.

Scientific acceptance happens per problem before artifacts are frozen. Document verification later checks content coverage, numerical consistency, compilation, references, and PDF quality; it does not retroactively replace scientific review. New contract-v2 projects do not wait for manual repair: stay within bounded automatic repair/replan instructions, or fail clearly when evidence or indispensable input cannot be recovered.

Run generated model code before reporting a problem complete. Use actual result files and figures as the only source for numerical claims. Every schema-v2 `verification.json` includes `figures`; each generated figure must declare its accepted-claim purpose, generator, real data paths, SciencePlots/API stack, vector master, PNG preview, language, and completed visual checks. Never use bundled template simulation, preview images, examples, or `*_replica` outputs as paper evidence. Ordinary charts use the pinned SciencePlots + Seaborn/Matplotlib stack; existing complex templates are layout references only when the Planner declared their scientific purpose. Report final success only when the verification prompt is active, `reports/VERIFY_REPORT.md` says `PASS`, and the compiled PDF exists and is non-empty.

For problem stages, benchmark one representative computation before a long loop. Keep search and acceptance predicates identical, distinguish numerical errors from mathematical infeasibility, and stop when the current problem artifacts are complete.

If optional Draw.io is unavailable, omit nonessential conceptual diagrams and record that limitation. Do not block numerical modeling, paper generation, or PDF validation solely because Draw.io is missing.
