# Workflow Stability Audit And Repairs

## Scope

Reviewed the Pi workflow at base commit `6919737`: import/start, Inventory, Method, Spike, Method Audit, Candidate, Scientific Review, paper planning/writing/verification, pause/resume/cancel, signed replay, paper continuation, and Vue task navigation. The user then authorized implementation, prioritizing smooth, bounded operation without relaxing scientific acceptance.

The initial audit used isolated temporary workspaces and mock RPC/process schedules. Two independent read-only reviews were cross-checked against source and executed reproductions. No real task was marked successful, no historical evidence was edited, and no model run was launched for this repair.

## Findings And Implemented Repairs

| Priority | Finding | Repair and regression evidence |
| --- | --- | --- |
| P1 | Signed review replay accepted changed candidate bytes. | Sign the reviewed workspace hashes and exact pre-transition Host outputs. Verify the unchanged evidence set before restoring only signed Host outputs and replaying. Changed candidate regression now fails before acceptance. |
| P1 | Scientific method/blocked rejection could dispatch method attempt 100. | Blocked is terminal. Permit one scientific method replan and enforce the existing three ordinary Method Audit ceiling before every ordinary revision dispatch. No budget increase. |
| P1 | Claim renaming bypassed ordinary A-to-B downgrade checks; downgrade-only could change top-level method specifications. | Ordinary revisions preserve the complete claim-ID/evidence-level map. Dedicated downgrade retains the base card's domain, witness, exclusion, cost and Spike specifications as well as existing non-claim problem fields. |
| P1 | Cancel during subprocess creation allowed a late initial prompt and overwrote cancelled state. | Serialize lifecycle controls, signal stopping before teardown, stop stale transitions, fence startup after process creation, and refuse resume while the old runner lives. |
| P1 | Broken RPC pipe prevented cancellation reaching cleanup. | Persist cancellation independently of the best-effort abort RPC, then finish process-tree teardown. Broken-pipe regression passes. |
| P1 | Rejected prompt left the task running without useful work or a deadline. | Correlate RPC responses by request ID. Track prompt preflight acknowledgements with a 30-second deadline; rejection/timeout produces a durable resumable pause. Clear pending acknowledgement timers on teardown. |
| P1 | Crash after revision ledger version increment failed before replay could restore the old card version. | Restore verified pending transitions before Method Card lookup and before reconstructing resume prompts. Failure-injection test advances to the intended revision. |
| P1 | Supplemental Spike after primary reuse looked up the old version. | Resolve the effective supplemental flag once and use it consistently for version, coverage and validation. Primary reuse still works. |
| P1 | Windows case-alias uploads and uploaded user_notes.md were silently overwritten. | Reject casefold duplicates and note collisions, and create uploads exclusively. No overwritten source is silently accepted. |
| P2 | Concurrent Job teardown fell through to taskkill and raised process-not-found during pause. | Single-flight teardown; confirm actual process exit for a failed taskkill, while still reporting a surviving process. Includes mocked race and real Windows Job Object pause/resume tests. |
| P2 | Startup user constraints disappeared from reconstructed prompts/downstream context. | Persist requirements in project metadata and a unique input Markdown file, list them in the manifest and scientific/paper contexts, and retain the metadata during explicit paper continuation. |
| P2 | Empty result/verification JSON objects skipped deterministic validation. | Distinguish missing/invalid JSON (`None`) from parsed empty dictionaries; empty candidate fields now fail protocol checks. |
| P2 | Paper coverage anchors could live entirely in an unused chapter. | Require manifest chapters to be reachable from the master and anchors to exist in uncommented reachable source. |
| P2 | A manuscript with citations but no bibliography items escaped reference checks. | Strict v3 checks undefined citation keys even when no bibitem exists. Legacy behavior remains separate. |
| P2 | Task-route reuse kept old messages/socket while controls referenced the new ID. | Key routed views by path, ignore callbacks from retired connections, and poll status at page scope rather than per tab. Desktop/mobile browser tests exercise task switching and polling while the PDF tab is open. |

## Runtime And UI Efficiency

- Workflow and PDF views share one page-owned status request, with no overlapping requests and no stale response application after unmount.
- PDF URLs carry an artifact revision token so a newly compiled PDF is not hidden by an unchanged iframe URL. Non-completed tasks display a draft label.
- The page-local timer is labelled as time spent viewing, not claimed as task compute time.
- v3 uses its existing authoritative cancellable full-page renderer rather than additionally calling synchronous Poppler readability probes inside Writing and Verify gates. Legacy v1/v2 checks are preserved.
- No additional production dependencies or speculative caching layers were introduced.

## Reproducible Checks

```text
.venv-pi/Scripts/python.exe -m unittest discover -s pi/tests -p "test_*.py"
.venv-pi/Scripts/python.exe -m compileall -q pi scripts/continue_paper.py
.venv-pi/Scripts/python.exe -m pip check
cd frontend
pnpm lint
pnpm build
```

Browser test (existing local Vite server, installed Edge and Playwright):

```text
PLAYWRIGHT_MODULE=<installed-playwright-path> node frontend/tests/task-lifecycle.cjs
```

The browser test intercepts task APIs and WebSockets and does not mutate real tasks. It checks 1440x1000 and 390x844 viewports, task route identity, one status poll on an open PDF tab, draft-to-accepted display transitions and absence of page errors. Screenshots are written to the invocation directory. The Windows lifecycle test starts a real Python RPC stub in a real Job Object, not a paid model process.

## Final Verification

- Full backend suite: **150 tests passed in 40.148 seconds**, including actual LaTeX/Poppler integration and the real Windows RPC-stub Job Object pause/resume test.
- Python compileall and pip check passed; frontend lint and production build passed; git diff whitespace check passed.
- Desktop/mobile browser regression passed after the final frontend changes. A real read-only browser visit through history opened task `054ac51c59c3`, displayed failed status and the draft PDF with its revision URL, and produced no page errors.
- After confirming no running/starting tasks, the loopback Bridge was restarted. It returned 26 historical tasks; hashes of all persisted project records and the existing eight-page PDF were unchanged. Service status reported Bridge and Pi available.
- No real model task was launched or resumed by these tests/deployment checks. The existing PDF remains available, but the original task is still failed rather than silently relabelled accepted.

## Safety And Remaining Limits

- Old pending transitions without signed reviewed-file bindings fail closed. They are not upgraded by trusting current artifact bytes; no automatic historical migration is attempted.
- Successful historical tasks, their hashes and reviewer verdicts are unchanged. Failed tasks remain failed; explicit continuation creates a separate provenance-linked workspace and never imports the previous manuscript.
- The paused/running application still shares the local user's execution environment. Stage context prompts and compiler-directory instructions are not a filesystem sandbox. The Windows Host locks, Job Objects, integrity gates and evidence hashes remain necessary.
- Literal LaTeX/Typst source traversal is not a full typesetting-language interpreter. A real Document Reviewer must still inspect the actual rendered manuscript, content, citations and scientific figures. Bibliography validation currently targets explicit bibitem/cite contracts.
- An initially suspected Windows `os.kill(pid, 0)` termination issue was not reproduced against the deployed Python 3.11 using a disposable child; it was excluded from confirmed findings rather than reported as a proven defect.
- The deterministic full-chain integration still uses supplied reviewer verdicts. Passing tests and a readable PDF do not establish arbitrary real-problem autonomous readiness. Real representative acceptance and cumulative integration review remain separate work.
- Previously identified provider-key revocation remains an external action. No secrets are included here.
