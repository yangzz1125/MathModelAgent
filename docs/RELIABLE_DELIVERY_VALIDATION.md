# Reliable Delivery Repair Validation

## Scope

- Base: `2365c06e061fe02f7a30ba5de547055ff2bfff7b`.
- Repair branch: `fix/workflow-reliable-delivery`.
- Isolated checkout: `E:/tmp/mathmodel-reliable-delivery`.
- Production checkout remained `pi-integration@3224ee8` throughout repair and validation. The owner later authorized a tested fast-forward merge into `pi-reliability-efficiency-refactor`; no production restart, history migration or manuscript editing is part of that merge.

## Repairs

1. Host cleanup failures retain native process/Job ownership, persist the cleanup fence, terminate the Pi peer, and cannot enter ordinary solver repair. Cancelling a transition cannot hide its cleanup failure. Later teardown can reclaim the retained process without silently clearing the durable fence.
2. Closed transport recovery confirms cleanup before persisting paused state and entering the existing real resume method. Manual recovery remains paused. Resume validation errors no longer falsely imply unconfirmed cleanup. Existing restart limits are unchanged.
3. Evidence hashes include executable bytecode and cache directories. Old snapshots with previously omitted executable files fail closed instead of silently accepting a stale receipt. Ordinary unchanged computation still uses its cache.
4. Server-side freeform prompt validation rejects both v3 and v4. Independent lifecycle controls and legacy behavior remain separate.
5. Task histories replace old cached fragments; only deltas received during the current HTTP request override its snapshot. Retired requests/connections are ignored and successful reconnect fetches missed history. Existing indexed stream updates and delivery status mappings remain.
6. Fixed the existing message-index lint violation without changing rules or adding application dependencies.

7. A real-case failure exposed exhausted provider errors being mistaken for normal settled handoffs. The supervisor now routes a final error/aborted assistant turn to bounded failure handling after Pi's own retries, with no additional Host restart or scientific repair prompt. Successful provider retries can still settle normally. Scientific rejection JSON is retained separately so infrastructure errors do not erase it.
8. The opt-in benchmark used an unregistered cancellation route. It now calls `/modeling/{id}/cancel`, checks the final task status, and reports cleanup failure rather than suppressing it. An isolated deadline watcher protected the already-running case; it did not need to cancel because the case finished early.
9. Formal delivery packaging is now separate from raw workspace download. It accepts only a completed, Host-approved PDF whose hash still matches the visual evidence, copies user-facing paper sources/code/input/results/figures through a denylist, emits a Chinese README and SHA-256 manifest, and atomically publishes the ZIP. Word, runtime state, sessions, logs, caches and secrets are excluded.

## Engineering Evidence

- New regression file: `pi/tests/test_reliable_delivery.py`.
- Before fixes: 7 tests, 9 failing assertions/subtests reproduced the transport, cleanup, bytecode and route defects.
- After all fixes and delivery packaging, the complete backend suite ran **261 tests in 112.990 seconds, OK with 2 skips**. The two skips are existing Windows symlink-permission tests; native Windows Job Object lifecycle tests ran.
- Frontend: 6 unit tests passed; `npm run lint` and `npm run build` passed.
- `frontend/tests/task-message-recovery.cjs`: Edge headless passed at 1440x1000 and 390x844 for revisits, reconnect history, delta/snapshot races, retired responses and accepted-warning/partial display, with zero page errors.
- `git diff --check` passed. Existing Python environment `pip check` passed.
- Tests used isolated temporary workflows, actual lightweight Python subprocesses, fake RPC peers and real TeX/page rendering; no scientific model calls were used for these checks.

Commands (from the isolated checkout):

```text
MATHMODELAGENT_ROOT=E:/tmp/mathmodel-reliable-delivery LOCALAPPDATA=E:/tmp/mathmodel-reliable-state E:/MathModelAgentPi/.venv-pi/Scripts/python.exe -m unittest discover -s pi/tests -p 'test_*.py'
npm --prefix frontend test
npm --prefix frontend run lint
npm --prefix frontend run build
```

Browser test requires an installed Playwright through PLAYWRIGHT_MODULE and a local built preview through TEST_BASE_URL. All Bridge/WebSocket traffic is intercepted. TEST_OUTPUT_DIR selects screenshot output.

Logs: `E:/tmp/reliable-red.log`, `E:/tmp/reliable-final-validation.log`, `E:/tmp/reliable-build.log`. Browser screenshots: `E:/tmp/reliable-1440.png`, `E:/tmp/reliable-390.png`. Additional regressions cover provider-error vs successful-retry handoffs, preserved scientific rejection, benchmark cancellation/cleanup acknowledgement, and delivery-package acceptance/denylist behavior.

Delivery-package smoke evidence used completed task `7a37ac0de57f`. The ZIP had 21 entries (19 manifest-tracked files plus `manifest.json` and an empty `reports/` output directory), passed `ZipFile.testzip()`, excluded internal/runtime files, reran `code/q1/solve.py` successfully after extraction, and rebuilt the paper with two XeLaTeX passes to an 8-page PDF. The smoke archive is `E:/tmp/MathModelAgent-7a37ac0de57f-delivery-test.zip`; the source workspace was not modified.

## Live Case: Partial, Acceptance Failed

- Task: `1e595eb13fa5`, created from the original single-bakery problem, no inherited accepted results.
- Workspace: `E:/MathModelingAssistant/data/reliable-delivery-live/workspaces/1e595eb13fa5`.
- Model profiles: `openai/gpt-5.6-sol` high for planning/review, `openai/gpt-5.6-luna` high for solving/writing.
- Balanced, Chinese, CUMCM, LaTeX; one case, 1200-second wall deadline, no automatic fresh-case retry.
- A prerequisite launch stopped before creating a task because the benchmark's `httpx` dependency was absent. Installed `httpx==0.28.1` only into the isolated harness `python-deps` directory; production venv and application dependency declarations were unchanged.
- The actual case started after this prerequisite was addressed. Status, timings and outputs are recorded by the isolated controller under `E:/MathModelingAssistant/data/reliable-delivery-live/`.
- Benchmark elapsed: **999.265 seconds (16 minutes 39 seconds)**. Runtime active time: 994.484 seconds; planning 46.109 seconds. No runtime restarts, 7 Host prompts, 14 observed provider-retry events. This is not an exact model API request or billing count.
- Actual artifacts: candidate JSON, independent validation JSON, eight-capacity CSV, SVG figure and a Markdown result narrative. No compiled paper PDF; no accepted problem outcome; no document review.
- Baseline candidate metrics match x=40, y=20, profit=2000 and breakpoints 40/160. CSV matches the eight requested capacities. Independent validation reports passed, but this did not substitute for the scientific Reviewer.
- The first scientific Reviewer returned `revise`: the generated F=40 dual multiplier interval was incorrectly `[20/3,20]` rather than `[20/3,40]`; one-sided economic marginal directions were reversed; labor shadow-price explanations needed correction. These are genuine mathematical/content issues. The assistant did not edit the generated answers to bypass review.
- The subsequent repair and later review requests received HTTP 503 `Service temporarily unavailable`. Earlier solving also recorded `terminated`/`Connection error.` provider failures. The historical run eventually ended `partial`, `q1=unresolved`, with `quality_passed=false`, `hung=false`.
- One earlier result-schema repair corrected missing `figures`. The successful compute receipt records solver 0.188 seconds and validator 0.172 seconds. Other attempted compute work is not fully represented by this success-only receipt; do not report 0.360 seconds as total runtime or total computation across attempts.
- A post-rejection provider failure incorrectly allowed one old-receipt cache hit and another review attempt. The provider-error handoff repair above was implemented and verified **after** this case. It has not been real-model retested; the historical task remains unchanged.
- SVG rendered for inspection outside the workspace, with no clipped text bounds. Its second breakpoint label sits close to the plotted line and its labels are English. This is not a final Chinese-paper quality certificate.
- Confirmed PIDs for Pi (31984), Bridge (22688), benchmark (78980), launcher/controller/deadline watcher were absent after completion. Durable `cleanup_required=false`. Isolated port/service stopped; production service unchanged.

### Live Evidence

`E:/MathModelingAssistant/data/reliable-delivery-live/` contains `benchmark.jsonl`, `run-status.json`, the original sessions and task files, `figure-inspection.png`, and controller logs. The initial missing-httpx preflight is separately preserved in `preflight-failed.json` and incurred no model task.

### Deployment Verdict

The owner authorized a tested fast-forward merge into `pi-reliability-efficiency-refactor`. **This does not replace or restart the production `pi-integration` service.** The representative real-model attempts did not reach document acceptance because of provider failures/cancellation, so deployment remains a separate explicit decision.

## Follow-up Runs

The user explicitly authorized fresh retries after reporting the upstream limit recovered. No terminal task was resumed or overwritten.

### R2: Upstream Still Rate-Limited

- Task `5522aa9431d7`, separate workspace under `E:/MathModelingAssistant/data/reliable-delivery-live-r2/`.
- Planning received HTTP 429 `Upstream rate limit exceeded, please retry later`, then three HTTP 503 responses.
- Runtime active time 92.156 seconds; benchmark 95.672 seconds. One Host prompt, zero Host restarts, no solver/results/paper.
- The new provider-error route stopped without requesting additional scientific work. `retry_reserved=false`, cleanup fence false; all recorded task/Bridge/controller PIDs and port 8017 were confirmed gone.
- A queued benchmark cancellation saw the temporary recovery pause and overwrote the subsequent failure status with cancelled. Historical evidence is retained unchanged. Fixed afterwards by rechecking cancellability inside the existing control lock; benchmark now records final status/metrics after cleanup rather than leaving the intermediate paused snapshot as its final result. Offline regression covers failed/partial/completed/completed-with-warnings/cancelled races.

### R3: Partial After Provider Failure

- Task `4bdd88911390`, separate workspace under `E:/MathModelingAssistant/data/reliable-delivery-live-r3/`.
- Planning, solver and validator artifacts completed with the expected bakery metrics, but a result-protocol repair was followed by HTTP 503 provider failure.
- Terminal status is `partial`; no accepted paper was produced and the source task remains unchanged.

### R4 and Startup Probe

- Task `0e9f9fe9f794` used DeepSeek profiles and was cancelled by the owner during solve; its terminal state and workspace are preserved under `reliable-delivery-live-r4/`.
- The following startup probe under `reliable-delivery-live-r5/` failed with `TypeError: fetch failed` before creating a task and incurred no model workflow.

## Boundaries

Auxiliary subagent independent review was not retried: the previously observed extension startup problem is outside this approved repair scope. This does not remove the actual program's scientific/document Reviewer gates. Linux behavior and arbitrary contest readiness are not claimed. One successful representative problem would be evidence for that path only.
