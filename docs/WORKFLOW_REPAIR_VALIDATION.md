# Concentrated Workflow Repair Validation

This repair addresses the ten findings in the offline mandatory-figure failure review. It is not another paid modeling run and does not complete repair-plan Batches 7 or 8.

## Changes

| Finding | Implemented correction |
| --- | --- |
| R1: integrity failures enter Producer repair | v3 integrity and scientific-acceptance failures terminate before Candidate, Writing, Paper Planning, or document-repair dispatch. |
| R2: optional/unbound page evidence | Host counts the actual PDF with pdfinfo and renders all pages with pdftoppm at 160 DPI. Color/grayscale pages and PDF hashes are persisted in Host state before the Reviewer snapshot and rechecked before completion. Log claims and Writer screenshots are not authoritative. Rendering has bounded subprocess timeouts and a persistent cancellation signal, including pause/resume races. |
| R3: replacement scientific figures | v3 Paper Plan must cover accepted vector masters, manifest figures must match the claim's plan, and reachable literal paper-source inclusions must use the planned figures. Paper-local replacements and unplanned problem-owned scientific figures are rejected. |
| R4: underspecified Paper Planning types | Initial and repair prompts share the same typed example. The validator reports sibling field-type failures together. The single repair allowance is unchanged. |
| R5: contradictory downgrade instructions | The dedicated authorized downgrade prompt reuses the neutral Method contract, not the ordinary revision's no-downgrade policy. |
| R6: supplemental coverage/accounting | Spike initial and repair prompts share one contract and one selected-ID calculation. Empty selected categories remain empty. Main and supplemental retain the existing combined two-repair ceiling. The current repair state is distinct from the cumulative counter, so a first supplemental probe is not mistaken for a repair of nonexistent artifacts. |
| R7: exhausted compute budget | No further compute-dependent repair is scheduled at zero remaining budget; no extra one-second window is granted. Active tool time counts against the budget. Host-measured excess is checked even if a watchdog callback loses a completion race. |
| R8: paper deliverables in Method outputs | Prompts distinguish downstream paper delivery from Execution-owned files. Boundary errors name offending paths and the allowed directories/exact report filename. |
| R9: frozen files treated as scientific proof | Prompts receive eligible scientific paths separately from general context. Strict v3 evidence validation excludes audit/receipt files and other problems. |
| R10: incomplete tests and hidden repairs | Offline v3 integration now traverses Inventory through Host completion with a required scientific figure, CSV-backed generator, actual vector/PNG files, twice-compiled LaTeX, and a real two-page PDF. Added negative tests and API/UI candidate-repair counters. |

## Verification

Commands use the existing Windows/Python 3.11 environment without dependency upgrades:

```text
.venv-pi/Scripts/python.exe -m unittest discover -s pi/tests -p "test_*.py"
.venv-pi/Scripts/python.exe -m compileall -q pi
.venv-pi/Scripts/python.exe -m pip check
cd frontend
pnpm lint
pnpm build
```

The final full suite passed all 127 tests in 32.469 seconds. compileall, pip check, frontend lint/build, and git diff --check also passed. After confirming there were no starting/running tasks, the loopback Bridge was restarted with the corrected code: /status and /models returned HTTP 200, and the historical failed task remained failed while the new repair counter appeared in its status response. The mandatory-figure integration uses test-supplied Reviewer verdicts, but does not mock the scientific artifact gates, manifest checks, LaTeX compiler, PDF renderer, completeness receipt, or final Host state transition. It verifies two physical pages and unchanged frozen evidence at completion. This demonstrates deterministic integration, not autonomous scientific reasoning or visual-quality acceptance by a real model.

Other checks cover terminal integrity handling across repair routes, aggregated Paper Plan list-type errors, forbidden audit evidence, paper-local figure substitution even when the manifest names the original, missing/tampered Host page evidence, prompt contradictions, exhausted Spike repair budgets, the status API's candidate repair count, and pause during Host rendering.

## Boundaries And Compatibility

- No failed historical workspace is resumed or migrated; no scientific verdict or frozen hash is manually accepted in a real task.
- No model/semantic attempt limit, Spike repair allowance, or scientific evidence level is relaxed.
- Strict scientific figure/evidence bindings and Host-render acceptance are v3-only. v1/v2 acceptance routes remain covered; legacy log/page checks remain available and also recognize common LaTeX byte-count log syntax.
- Source checks support literal includegraphics/image paths and local source inclusions. They are not a TeX interpreter and cannot replace Reviewer inspection of the actual PDF or scientific content.
- The Host rendering stage requires installed pdfinfo/pdftoppm, checked by preflight; no new Python package is introduced.
- Font-fallback/deprecation warnings and an outdated Browserslist dataset remain non-failing environment notices. Dependencies were not upgraded to silence them.
- The real paid representative run and final cumulative integration acceptance remain outstanding.
