"""Runtime-only integration for TaskRuntime; scientific gates stay in bridge.py.

Installed by the guarded source transformer shipped with this change. This is
not a monkey patch. Core method names are explicit and normal Python inheritance
is used to separate transport/lifecycle supervision from modeling contracts.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pi.runtime_support import (
    BudgetLedger, ClientFanout, RuntimeBudgetError, RuntimePolicy, TurnLease,
    atomic_json, bounded_wait, normalize_json_envelope, stop_remaining_process_group,
)

ACTIVE = {"starting", "running"}
TERMINAL = {"completed", "cancelled", "failed", "paused", "partial", "completed_with_warnings"}
NON_RETRYABLE = (
    "budget_exhausted", "artifact_changed:", "scientific_acceptance:", "provider_error:",
    "unauthorized", "invalid api key", "authentication", "permission denied", "missing credentials", "no api key",
)


@dataclass
class RuntimeState:
    policy: RuntimePolicy
    ledger: BudgetLedger
    epoch: int = 0
    turn: TurnLease | None = None
    monitor: asyncio.Task[Any] | None = None
    recovery: asyncio.Task[Any] | None = None
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    fanout: ClientFanout | None = None
    started: float = 0.0
    transition_started: float | None = None
    checkpoint_at: float = 0.0
    exit_error: str = ""
    cleanup_required: bool = False
    host_cleanup_error: Any = None
    recovery_pending: bool = False
    provider_message_open: bool = False
    provider_error: str = ""
    posix_pgid: int | None = None
    log_task: asyncio.Task[Any] | None = None
    log_dirty: bool = False
    log_error: str = ""
    message_index: dict[str, int] = field(default_factory=dict)


class RuntimeSupervisionMixin:
    """Requires the documented TaskRuntime methods preserved by apply.py."""

    def _safety_state(self) -> RuntimeState:
        state = getattr(self, "_runtime_safety_state", None)
        if state is None:
            try:
                project = self._project()
            except (OSError, ValueError):
                project = {}
            saved = project.get("runtime_metrics") or {}
            state = RuntimeState(
                RuntimePolicy.from_environment(), BudgetLedger.restore(saved),
                cleanup_required=bool(saved.get("cleanup_required")) if isinstance(saved, dict) else False,
            )
            self._runtime_safety_state = state
        return state

    def _safety_stage(self) -> str:
        try:
            workflow = self._project().get("workflow") or {}
        except (OSError, ValueError):
            workflow = {}
        return str(workflow.get("current") or "legacy")

    def _safety_fanout(self) -> ClientFanout:
        state = self._safety_state()
        if state.fanout is None:
            state.fanout = ClientFanout(state.policy, self.clients.discard)
        return state.fanout

    def _save_project(self, project: dict[str, Any]) -> None:
        state = self._safety_state()
        metrics = state.ledger.snapshot()
        metrics["schema_version"] = 1
        metrics["cleanup_required"] = state.cleanup_required
        metrics["log_error"] = state.log_error
        metrics["token_usage_complete"] = (
            state.ledger.assistant_messages > 0
            and state.ledger.usage_messages == state.ledger.assistant_messages
            and state.ledger.interrupted_turns == 0
            and not state.provider_message_open
        )
        if state.fanout:
            metrics["browser_disconnects"] = state.fanout.dropped
            metrics["coalesced_browser_updates"] = state.fanout.coalesced
        # Always merge the current metrics, even when a workflow transition is
        # saving a project object that was read before an asynchronous operation.
        project["runtime_metrics"] = metrics
        if (project.get("workflow") or {}).get("contract_version") == 4:
            from pi.efficient_workflow import sign_project
            sign_project(self.task_id, project)
        self._save_project_core(project)

    async def publish(self, message: dict[str, Any], *, persist: bool = True) -> None:
        state = self._safety_state()
        async with self._write_lock:
            if len(state.message_index) != len(self.messages):
                state.message_index = {str(item.get("id")): i for i, item in enumerate(self.messages)}
            key = str(message.get("id"))
            index = state.message_index.get(key)
            if index is None:
                state.message_index[key] = len(self.messages)
                self.messages.append(message)
            else:
                self.messages[index] = message
            if persist:
                state.log_dirty = True
                if state.log_task is None or state.log_task.done():
                    state.log_task = asyncio.create_task(self._persist_message_snapshots(), name="pi-message-writer")
        self._safety_fanout().broadcast(self.clients, message)

    async def _persist_message_snapshots(self) -> None:
        state = self._safety_state()
        # One writer for the lifetime of this TaskRuntime, including Pi restarts.
        # It is intentionally not cancelled during process teardown: cancelling
        # to_thread cannot stop a disk write and would allow reordered snapshots.
        try:
            while state.log_dirty:
                await asyncio.sleep(0.1)
                state.log_dirty = False
                snapshot = list(self.messages)
                def write() -> None:
                    self._write_messages(json.dumps(snapshot, ensure_ascii=False, indent=2))
                await asyncio.to_thread(write)
            state.log_error = ""
        except Exception as exc:
            state.log_error = str(exc)[:1000]
            # A failed transcript write is observability degradation, not a
            # reason to repeat a successful model call or modify accepted data.

    async def _flush_message_snapshots(self) -> None:
        state = self._safety_state()
        task = state.log_task
        if task and task is not asyncio.current_task() and not task.done():
            if await bounded_wait({task}, state.policy.cancel_seconds):
                state.log_error = "transcript_flush_timeout; writer remains single-flight"

    async def send_rpc(self, payload: dict[str, Any]) -> None:
        state = self._safety_state()
        # Includes waiting for another writer and drain(), not just the reply.
        async with asyncio.timeout(state.policy.write_seconds):
            async with state.send_lock:
                process = self.process
                if not process or getattr(process, "returncode", None) is not None or not process.stdin:
                    raise RuntimeError("Pi process is not running")
                process.stdin.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
                await process.stdin.drain()

    async def rpc_command(self, payload: dict[str, Any], *, timeout: float = 15) -> dict[str, Any]:
        command = str(payload.get("type") or "")
        if not command or timeout <= 0:
            raise ValueError("RPC requires a command and a positive deadline")
        state = self._safety_state()
        state.ledger.rpc_commands += 1
        request_id = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self._pending_rpc[request_id] = future
        try:
            async with asyncio.timeout(timeout):
                await self.send_rpc({**payload, "id": request_id})
                response = await future
        finally:
            self._pending_rpc.pop(request_id, None)
            if not future.done():
                future.cancel()
        if not response.get("success", False):
            raise RuntimeError(f"Pi {command} failed: {response.get('error', 'unknown error')}")
        if command in {"new_session", "switch_session"} and (response.get("data") or {}).get("cancelled"):
            # success=true + cancelled=true does NOT mean a fresh session exists.
            raise RuntimeError(f"Pi {command} was cancelled by an extension")
        return response

    async def prompt(self, text: str, *, initial: bool = False) -> None:
        state = self._safety_state()
        if self._stopping or self.status in TERMINAL or state.cleanup_required:
            raise asyncio.CancelledError
        if state.turn and not state.turn.settled:
            raise RuntimeError("A supervised prompt is already active; do not queue duplicate workflow work")
        stage = self._safety_stage()
        now = time.monotonic()
        state.ledger.tick(now, stage)
        try:
            state.ledger.reserve_prompt(stage, state.policy)
        except RuntimeBudgetError as exc:
            self._request_runtime_recovery(str(exc))
            raise
        workflow = self._project().get("workflow") or {}
        seconds = state.policy.review_seconds if self._reviewer_capability(workflow) else state.policy.turn_seconds
        request_id = uuid.uuid4().hex
        state.turn = TurnLease(request_id, now, seconds)
        state.provider_error = ""
        state.transition_started = None
        self._last_assistant_text = ""
        self.set_status("running")
        payload: dict[str, Any] = {"id": request_id, "type": "prompt", "message": text}
        # Workflow prompts are issued only after agent_settled. Do not use steer:
        # an unexpected active run should reject, not queue duplicate computation.
        try:
            await self.send_rpc(payload)
        except BaseException:
            state.turn = None
            state.transition_started = time.monotonic()
            raise

    async def _handle_event(self, event: dict[str, Any]) -> None:
        if not isinstance(event, dict):
            return
        state = self._safety_state()
        kind = event.get("type")
        now = time.monotonic()
        turn = state.turn
        if turn:
            limit = state.policy.tool_seconds
            if kind == "tool_execution_start" and event.get("toolName") == "bash":
                specified = self._current_runtime_limit()
                if isinstance(specified, (int, float)) and specified >= 0:
                    # Let the existing tool-budget abort/repair path run first.
                    limit = specified + state.policy.cancel_seconds
            turn.observe(event, now, limit)
            if kind == "response" and event.get("id") == turn.request_id and not event.get("success", False):
                self._request_runtime_recovery("prompt_rejected: " + str(event.get("error") or "unknown"))
        if kind == "agent_settled":
            if turn is None or turn.settled or self._stopping or self.status not in ACTIVE:
                return
            # Atomically consume the lease BEFORE creating an asynchronous task.
            turn.settled = True
            state.transition_started = now
            state.ledger.tick(now, self._safety_stage())
            if state.provider_error:
                # Pi already exhausted its own provider retries. This is not a
                # successful handoff or a malformed scientific verdict.
                self._request_runtime_recovery("provider_error: " + state.provider_error)
                return
            if state.recovery_pending:
                state.ledger.recovered_turns += 1
                state.recovery_pending = False
            task = asyncio.create_task(self._settled(), name="pi-workflow-transition")
            self._transitions.add(task)
            task.add_done_callback(self._transitions.discard)
            return
        if kind == "auto_retry_start":
            state.ledger.provider_retry_events += 1
        if kind == "message_start" and (event.get("message") or {}).get("role") == "assistant":
            state.provider_message_open = True
        if kind == "message_end":
            message = event.get("message") or {}
            if isinstance(message, dict) and message.get("role") == "assistant":
                state.ledger.record_usage(message)
                state.provider_message_open = False
                state.provider_error = (
                    str(message.get("errorMessage") or message.get("stopReason"))[:2000]
                    if message.get("stopReason") in {"error", "aborted"} or message.get("errorMessage")
                    else ""
                )
        await self._handle_event_core(event)
        if kind == "message_end" and self._reviewer_capability(self._project().get("workflow") or {}):
            normalized = normalize_json_envelope(self._last_assistant_text)
            if normalized != self._last_assistant_text:
                self._last_assistant_text = normalized
                state.ledger.normalized_envelopes += 1

    async def _read_stdout(self) -> None:
        process = self.process
        assert process and process.stdout
        epoch = self._safety_state().epoch
        while line := await process.stdout.readline():
            # A stream from an old process must never affect a new attempt.
            if process is not self.process or epoch != self._safety_state().epoch:
                return
            try:
                event = json.loads(line.decode("utf-8").rstrip("\r\n"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if isinstance(event, dict):
                await self._handle_event(event)
        if not self._stopping and self.status in ACTIVE:
            self._safety_state().exit_error = "pi_stdout_closed"
            raise RuntimeError("Pi stdout closed before task completion")

    async def run(self, prompt: str) -> None:
        state = self._safety_state()
        if self._stopping or state.cleanup_required:
            return
        state.epoch += 1
        epoch = state.epoch
        state.started = time.monotonic()
        state.exit_error = ""
        state.turn = None
        state.transition_started = None
        state.ledger.tick(state.started, self._safety_stage())
        state.monitor = asyncio.create_task(self._monitor_runtime(epoch), name="pi-runtime-supervisor")
        try:
            await self._run_core(prompt)
        finally:
            if state.monitor and state.monitor is not asyncio.current_task():
                state.monitor.cancel()
            state.ledger.tick(time.monotonic(), self._safety_stage(), active=False)
            if state.exit_error and not self._stopping:
                self._request_runtime_recovery(state.exit_error, closed=True)

    async def _monitor_runtime(self, epoch: int) -> None:
        state = self._safety_state()
        try:
            while epoch == state.epoch and not self._stopping and self.status in ACTIVE:
                now = time.monotonic()
                stage = self._safety_stage()
                state.ledger.tick(now, stage)
                reason = state.ledger.violation(stage, state.policy)
                host_deadline = getattr(self, "_host_operation_deadline", None)
                if reason is None and host_deadline is not None:
                    if now >= host_deadline:
                        reason = "host_operation_timeout"
                elif reason is None and state.turn and not state.turn.settled:
                    reason = state.turn.violation(now, state.policy)
                elif reason is None and state.transition_started is not None:
                    if now - state.transition_started >= state.policy.transition_seconds:
                        reason = "host_transition_timeout"
                elif reason is None and state.turn is None and now - state.started >= state.policy.startup_seconds:
                    reason = "pi_startup_timeout"
                if reason:
                    self._request_runtime_recovery(reason)
                    return
                if now - state.checkpoint_at >= state.policy.checkpoint_seconds:
                    self._save_project(self._project())
                    state.checkpoint_at = now
                await asyncio.sleep(state.policy.poll_seconds)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._request_runtime_recovery("supervisor_error: " + str(exc))

    def _request_runtime_recovery(self, error: str, *, closed: bool = False) -> None:
        state = self._safety_state()
        if state.recovery and not state.recovery.done():
            return
        if self._stopping or self.status == "cancelled" or (self.status not in ACTIVE and not closed):
            return
        state.recovery = asyncio.create_task(
            self._recover_runtime(error, state.epoch, closed=closed), name="pi-runtime-recovery",
        )

    async def _wait_with_errors(self, errors: list[str]) -> None:
        # Preserve scientific contract failures. Only errors explicitly labeled
        # as transport failures by the original bridge enter runtime recovery.
        if errors and all(str(error).startswith("rpc_error:") for error in errors):
            self._request_runtime_recovery("; ".join(map(str, errors)))
            return
        await self._wait_with_errors_core(errors)

    async def _pause_rpc_failure(self, error: str) -> None:
        # Never pause/resume while holding _transition_lock or reading stdout.
        self._request_runtime_recovery(error)

    async def _recover_runtime(self, error: str, epoch: int, *, closed: bool = False) -> None:
        state = self._safety_state()
        cleanup_confirmed = False
        try:
            async with self._control_lock:
                if epoch != state.epoch or self._stopping or self.status == "cancelled":
                    return
                if self.status not in ACTIVE and not closed:
                    return
                project = self._project()
                workflow = project.get("workflow")
                if not isinstance(workflow, dict):
                    # Legacy tasks have no safe durable stage to replay.
                    retry = False
                    stage = "legacy"
                else:
                    stage = str(workflow.get("current") or "legacy")
                    state.ledger.tick(time.monotonic(), stage)
                    retry = (
                        not any(token in error.lower() for token in NON_RETRYABLE)
                        and state.ledger.reserve_restart(stage, state.policy)
                    )
                    phase = self._current_phase(workflow)
                    if phase is not None:
                        phase["last_error"] = "runtime_recovery: " + error[:1500]
                state.ledger.faults.append({
                    "stage": stage, "reason": error[:2000], "retry_reserved": retry,
                    "at_unix": time.time(), "epoch": epoch,
                })
                state.ledger.faults = state.ledger.faults[-100:]
                self._save_project(project)  # reserve retries durably before restart
                if self.status in ACTIVE:
                    await self._pause("runtime_recovery: " + error[:1000])
                else:
                    self._stopping = True
                    await self._stop_runtime()
                if state.cleanup_required:
                    raise RuntimeError("cleanup is unconfirmed")
                cleanup_confirmed = True
                if self.status != "paused" and (retry or not state.policy.auto_recover):
                    project = self._project()
                    project.update(status="paused", paused_at=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                                   pause_reason="runtime_recovery: " + error[:1000])
                    phase = self._current_phase(project.get("workflow") or {})
                    if phase is not None:
                        phase["status_before_pause"] = phase.get("status") or "running"
                        phase["status"] = "paused"
                    self.status = "paused"
                    self._save_project(project)
                if not state.policy.auto_recover:
                    return  # explicit operator mode: remain paused
                if not retry:
                    await self._finalize_runtime_failure(error)
                    return
                # Existing _resume validates pending transition signatures and
                # rebuilds a stage-local prompt with the existing tool policy.
                state.recovery_pending = True
                await self._resume()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not cleanup_confirmed:
                state.cleanup_required = True
            self._stopping = True
            self.status = "failed"
            with contextlib.suppress(Exception):
                await self._quiesce_transitions()
                await self.terminate()
            # Do not start another worker when an old task might still write.
            with contextlib.suppress(Exception):
                project = self._project()
                project["status"] = "failed"
                prefix = "cleanup_or_resume_unconfirmed" if state.cleanup_required else "resume_failed"
                project["runtime_failure"] = f"{prefix}: {exc}"
                self._save_project(project)

    def _mark_cleanup_unconfirmed(self, reason: str) -> None:
        state = self._safety_state()
        state.cleanup_required = True
        self._stopping = True
        self.status = "failed"
        with contextlib.suppress(Exception):
            project = self._project()
            project["status"] = "failed"
            project["runtime_failure"] = reason[:2000]
            self._save_project(project)

    async def _quiesce_transitions(self) -> None:
        state = self._safety_state()
        rendering = self._host_render_cancel
        if rendering is not None:
            rendering.set()
        tasks = {
            task for task in self._transitions
            if task is not asyncio.current_task() and not task.done()
        }
        if not tasks:
            if state.cleanup_required:
                raise RuntimeError("Host cleanup is unconfirmed; resume is blocked")
            return
        if rendering is not None:
            # Do not cancel a coroutine wrapping to_thread(render): cancellation
            # would detach a still-writing thread. Wait for cooperative exit.
            pending = await bounded_wait(tasks, state.policy.cleanup_seconds)
        else:
            for task in tasks:
                task.cancel()
            pending = await bounded_wait(tasks, state.policy.cleanup_seconds if getattr(self, "_host_job_active", False) else state.policy.cancel_seconds)
        if state.cleanup_required:
            raise RuntimeError("Host cleanup is unconfirmed; resume is blocked")
        if pending:
            self._mark_cleanup_unconfirmed("Transition cleanup did not finish; automatic resume is blocked")
            raise RuntimeError("Transition cleanup did not finish; automatic resume is blocked")

    async def _terminate_process(self) -> None:
        state = self._safety_state()
        state.ledger.tick(time.monotonic(), self._safety_stage(), active=False)
        if state.turn and not state.turn.settled:
            state.ledger.interrupted_turns += 1
        state.turn = None
        state.provider_message_open = False
        if state.monitor and state.monitor is not asyncio.current_task():
            state.monitor.cancel()
        # Do not leave RPC callers waiting on replies from a dead process.
        for future in tuple(self._pending_rpc.values()):
            if not future.done():
                future.set_exception(ConnectionError("Pi process stopped"))
        self._pending_rpc.clear()
        try:
            try:
                if state.host_cleanup_error is not None:
                    from pi.compute_jobs import _cleanup_job
                    held = state.host_cleanup_error
                    await _cleanup_job(held.process, held.job)
                    state.host_cleanup_error = None
                    # A successful later teardown does not silently clear the durable fence.
            finally:
                await self._terminate_process_core()
                if state.posix_pgid is not None:
                    await stop_remaining_process_group(state.posix_pgid, state.policy.cleanup_seconds)
                    state.posix_pgid = None
            await self._flush_message_snapshots()
        except Exception as exc:
            self._mark_cleanup_unconfirmed("Process cleanup is unconfirmed: " + str(exc))
            raise
        finally:
            with contextlib.suppress(Exception):
                self._save_project(self._project())

    async def _finalize_runtime_failure(self, error: str) -> None:
        project = self._project()
        workflow = project.get("workflow") or {}
        self._stopping = True
        self.status = "failed"  # retain existing UI/API contract; delivery is separate
        project["status"] = "failed"
        project["runtime_failure"] = error[:2000]
        workflow["mode"] = "failed"
        phase = self._current_phase(workflow)
        if phase is not None:
            phase["status"] = "failed"
            phase["last_error"] = error[:2000]
        self._save_project(project)
        try:
            summary = await asyncio.wait_for(
                asyncio.to_thread(_write_partial_delivery, self.workspace, project, error),
                timeout=self._safety_state().policy.cleanup_seconds,
            )
        except Exception as exc:
            project["delivery_status"] = "unknown"
            project["delivery_error"] = str(exc)[:1000]
        else:
            project["delivery_status"] = summary["delivery_status"]
            project["delivery_report"] = "reports/RUNTIME_PARTIAL_REPORT.md"
        self._save_project(project)


def _write_partial_delivery(workspace: Path, project: dict[str, Any], error: str) -> dict[str, Any]:
    """Index real existing files. Never invent results or upgrade acceptance."""
    root = workspace.resolve()
    items: list[dict[str, Any]] = []
    for directory in ("code", "results", "figures", "paper", "reports"):
        base = root / directory
        if base.is_symlink() or not base.is_dir():
            continue
        for parent, dirs, files in os.walk(base, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not (Path(parent) / d).is_symlink())
            for name in sorted(files):
                path = Path(parent) / name
                if path.is_symlink() or name in {"RUNTIME_PARTIAL_REPORT.md", "RUNTIME_DELIVERY.json"}:
                    continue
                try:
                    relative = path.resolve().relative_to(root).as_posix()
                    size = path.stat().st_size
                except (OSError, ValueError):
                    continue
                if size:
                    items.append({"path": relative, "size_bytes": size, "validation": "not_assessed_by_runtime"})
                if len(items) >= 10000:
                    break
            if len(items) >= 10000:
                break
        if len(items) >= 10000:
            break
    summary = {
        "schema_version": 1,
        "delivery_status": "partial" if items else "failed",
        "execution_status": "failed",
        "scientific_acceptance": "unchanged",
        "reason": error[:2000],
        "current_stage": (project.get("workflow") or {}).get("current"),
        "artifacts": items,
        "listing_truncated": len(items) >= 10000,
        "notice": "Existing files are preserved, not certified. No completion or scientific PASS is inferred from file presence.",
    }
    reports = root / "reports"
    if reports.is_symlink():
        raise ValueError("Refusing to write a delivery report through a symlink")
    reports.mkdir(exist_ok=True)
    for name in ("RUNTIME_DELIVERY.json", "RUNTIME_PARTIAL_REPORT.md"):
        target = reports / name
        if target.is_symlink() or not target.resolve().is_relative_to(root):
            raise ValueError("Unsafe delivery report path")
    atomic_json(root / "reports" / "RUNTIME_DELIVERY.json", summary)
    report = root / "reports" / "RUNTIME_PARTIAL_REPORT.md"
    report.write_text(
        "# Partial delivery / runtime interruption\n\n"
        + "This is NOT a completed or scientifically accepted solution.\n\n"
        + "Current stage: " + str(summary["current_stage"]) + "\n\n"
        + "Reason: " + error[:2000] + "\n\n"
        + "Existing artifacts remain available in their original locations. "
        + "Their contents were not certified by this runtime report. "
        + "See RUNTIME_DELIVERY.json for the file inventory.\n",
        encoding="utf-8",
    )
    return summary
