"""Small, dependency-free reliability primitives for the Pi bridge (Python 3.11+).

These primitives do not accept scientific results, change tool permissions, or
synthesize model output. Deadlines require a responsive asyncio event loop.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import math
import os
import re
import signal
import sys
import tempfile
import time
from collections import OrderedDict
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Awaitable, Callable, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class RuntimePolicy:
    ack_seconds: float = 30.0
    write_seconds: float = 5.0
    idle_seconds: float = 240.0
    tool_seconds: float = 300.0
    turn_seconds: float = 900.0
    review_seconds: float = 360.0
    stage_seconds: float = 1500.0
    task_seconds: float = 5400.0
    startup_seconds: float = 60.0
    transition_seconds: float = 180.0
    poll_seconds: float = 1.0
    checkpoint_seconds: float = 15.0
    cleanup_seconds: float = 10.0
    cancel_seconds: float = 2.0
    websocket_seconds: float = 2.0
    websocket_pending: int = 64
    max_stage_restarts: int = 1
    max_task_restarts: int = 3
    max_stage_prompts: int = 12
    max_task_prompts: int = 120
    auto_recover: bool = True

    def __post_init__(self) -> None:
        for item in fields(self):
            value = getattr(self, item.name)
            if item.name == "auto_recover":
                if not isinstance(value, bool):
                    raise ValueError("auto_recover must be boolean")
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{item.name} must be numeric")
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{item.name} must be finite and nonnegative")
            if not item.name.endswith("restarts") and value == 0:
                raise ValueError(f"{item.name} must be positive")
            if isinstance(item.default, int) and not isinstance(item.default, bool):
                if not isinstance(value, int):
                    raise ValueError(f"{item.name} must be an integer")

    @classmethod
    def from_environment(cls) -> "RuntimePolicy":
        values: dict[str, Any] = {}
        for item in fields(cls):
            raw = os.environ.get("MATHMODEL_RT_" + item.name.upper())
            if raw is None:
                continue
            if isinstance(item.default, bool):
                if raw.lower() not in {"1", "0", "true", "false"}:
                    raise ValueError(f"invalid boolean for {item.name}")
                values[item.name] = raw.lower() in {"1", "true"}
            elif isinstance(item.default, int):
                values[item.name] = int(raw)
            else:
                values[item.name] = float(raw)
        return cls(**values)


@dataclass
class TurnLease:
    request_id: str
    started: float
    hard_seconds: float
    last_progress: float = 0.0
    acknowledged: bool = False
    settled: bool = False
    tools: dict[str, float] = field(default_factory=dict)
    assistant_seen: bool = False

    def __post_init__(self) -> None:
        self.last_progress = self.started

    def observe(self, event: dict[str, Any], now: float, tool_limit: float) -> None:
        kind = event.get("type")
        if kind == "response" and event.get("id") == self.request_id:
            self.acknowledged = bool(event.get("success"))
            if self.acknowledged:
                self.last_progress = now
        elif kind == "message_update":
            update = event.get("assistantMessageEvent") or {}
            if isinstance(update, dict) and update.get("delta"):
                self.last_progress = now
        elif kind in {"agent_start", "turn_start", "message_start", "message_end", "turn_end"}:
            self.last_progress = now
            if kind == "message_end":
                message = event.get("message") or {}
                self.assistant_seen |= isinstance(message, dict) and message.get("role") == "assistant"
        elif kind == "tool_execution_start":
            tool_id = str(event.get("toolCallId") or "")
            if tool_id:
                # Duplicate starts must not extend a tool's absolute deadline.
                self.tools.setdefault(tool_id, now + max(0.0, tool_limit))
            self.last_progress = now
        elif kind == "tool_execution_end":
            self.tools.pop(str(event.get("toolCallId") or ""), None)
            self.last_progress = now
        elif kind == "tool_execution_update":
            if str(event.get("toolCallId") or "") in self.tools:
                self.last_progress = now
        # Retry/queue/unknown events are not proof of useful progress. They never
        # extend the hard turn, stage, task, or tool deadline.

    def violation(self, now: float, policy: RuntimePolicy) -> str | None:
        if self.settled:
            return None
        if now - self.started >= self.hard_seconds:
            return "turn_deadline"
        if not self.acknowledged and now - self.started >= policy.ack_seconds:
            return "prompt_ack_timeout"
        if any(now >= deadline for deadline in self.tools.values()):
            return "tool_deadline"
        # A silent numerical job is allowed to use its compute lease. It is not
        # mistaken for an idle model merely because it does not print logs.
        if self.acknowledged and not self.tools and now - self.last_progress >= policy.idle_seconds:
            return "agent_idle_timeout"
        return None


@dataclass
class BudgetLedger:
    active_seconds: float = 0.0
    stage_seconds: dict[str, float] = field(default_factory=dict)
    prompts: int = 0
    stage_prompts: dict[str, int] = field(default_factory=dict)
    restarts: int = 0
    stage_restarts: dict[str, int] = field(default_factory=dict)
    rpc_commands: int = 0
    assistant_messages: int = 0
    usage_messages: int = 0
    provider_retry_events: int = 0
    recovered_turns: int = 0
    normalized_envelopes: int = 0
    interrupted_turns: int = 0
    tokens: dict[str, int] = field(default_factory=lambda: {
        "input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0,
    })
    faults: list[dict[str, Any]] = field(default_factory=list)
    _tick_at: float | None = None
    _stage: str = ""

    @classmethod
    def restore(cls, value: Any) -> "BudgetLedger":
        ledger = cls()
        if not isinstance(value, dict):
            return ledger
        for name in ("active_seconds", "prompts", "restarts", "rpc_commands",
                     "assistant_messages", "usage_messages", "provider_retry_events",
                     "recovered_turns", "normalized_envelopes", "interrupted_turns"):
            item = value.get(name)
            if isinstance(item, (int, float)) and not isinstance(item, bool) and math.isfinite(item) and item >= 0:
                setattr(ledger, name, float(item) if name == "active_seconds" else int(item))
        for name in ("stage_seconds", "stage_prompts", "stage_restarts", "tokens"):
            item = value.get(name)
            if isinstance(item, dict):
                target = getattr(ledger, name)
                for key, amount in item.items():
                    if isinstance(key, str) and isinstance(amount, (int, float)) and not isinstance(amount, bool) and math.isfinite(amount) and amount >= 0:
                        target[key] = float(amount) if name == "stage_seconds" else int(amount)
        ledger.faults = [x for x in value.get("faults", []) if isinstance(x, dict)][-100:] if isinstance(value.get("faults"), list) else []
        return ledger

    def tick(self, now: float, stage: str, *, active: bool = True) -> None:
        if self._tick_at is not None:
            delta = max(0.0, now - self._tick_at)
            self.active_seconds += delta
            self.stage_seconds[self._stage] = self.stage_seconds.get(self._stage, 0.0) + delta
        self._tick_at = now if active else None
        self._stage = stage

    def violation(self, stage: str, policy: RuntimePolicy, *, next_prompt: bool = False) -> str | None:
        if self.active_seconds >= policy.task_seconds:
            return "task_budget_exhausted"
        if self.stage_seconds.get(stage, 0.0) >= policy.stage_seconds:
            return "stage_budget_exhausted"
        if next_prompt and self.prompts >= policy.max_task_prompts:
            return "task_prompt_budget_exhausted"
        if next_prompt and self.stage_prompts.get(stage, 0) >= policy.max_stage_prompts:
            return "stage_prompt_budget_exhausted"
        return None

    def reserve_prompt(self, stage: str, policy: RuntimePolicy) -> None:
        if reason := self.violation(stage, policy, next_prompt=True):
            raise RuntimeBudgetError(reason)
        self.prompts += 1
        self.stage_prompts[stage] = self.stage_prompts.get(stage, 0) + 1

    def reserve_restart(self, stage: str, policy: RuntimePolicy) -> bool:
        if not policy.auto_recover or self.violation(stage, policy):
            return False
        if self.restarts >= policy.max_task_restarts or self.stage_restarts.get(stage, 0) >= policy.max_stage_restarts:
            return False
        self.restarts += 1
        self.stage_restarts[stage] = self.stage_restarts.get(stage, 0) + 1
        return True

    def snapshot(self) -> dict[str, Any]:
        return {item.name: getattr(self, item.name) for item in fields(self) if not item.name.startswith("_")}

    def record_usage(self, message: dict[str, Any]) -> None:
        self.assistant_messages += 1
        usage = message.get("usage")
        if not isinstance(usage, dict):
            return
        complete = True
        for name in self.tokens:
            value = usage.get(name)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                self.tokens[name] += value
            else:
                complete = False
        self.usage_messages += int(complete)


class RuntimeBudgetError(RuntimeError):
    pass


def normalize_json_envelope(text: str) -> str:
    """Remove only an unambiguous JSON markdown fence; never repair semantics."""
    match = re.fullmatch(r"\s*```(?:json)?\s*\n(.*?)\n```\s*", text, re.DOTALL | re.IGNORECASE)
    if not match:
        return text
    inner = match.group(1).strip()
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result
    def invalid(_: str) -> Any:
        raise ValueError("non-finite JSON constant")
    def finite_float(value: str) -> float:
        result = float(value)
        if not math.isfinite(result):
            raise ValueError("non-finite JSON number")
        return result
    try:
        value = json.loads(inner, object_pairs_hook=pairs, parse_constant=invalid, parse_float=finite_float)
    except (ValueError, TypeError, RecursionError):
        return text
    return inner if isinstance(value, dict) else text


def atomic_json(path: Path, value: Any) -> None:
    """Atomic replacement for UNLOCKED files only, never Windows Host handles."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as output:
            json.dump(value, output, ensure_ascii=False, indent=2, allow_nan=False)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        for attempt in range(4):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if attempt == 3:
                    raise
                time.sleep(0.025 * (attempt + 1))
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)


async def bounded_wait(tasks: set[asyncio.Task[Any]], seconds: float) -> set[asyncio.Task[Any]]:
    """Return unfinished tasks without waiting for cancellation acknowledgement."""
    if not tasks:
        return set()
    done, pending = await asyncio.wait(tasks, timeout=seconds)
    for task in done:
        if not task.cancelled():
            task.exception()  # retrieve exceptions; callers decide their semantics
    return pending


@dataclass
class _Client:
    socket: Any
    pending: OrderedDict[str, dict[str, Any]] = field(default_factory=OrderedDict)
    wakeup: asyncio.Event = field(default_factory=asyncio.Event)
    sender: asyncio.Task[None] | None = None


class ClientFanout:
    """One bounded, coalescing sender per browser; RPC reading never awaits it."""
    def __init__(self, policy: RuntimePolicy, on_drop: Callable[[Any], None]) -> None:
        self.policy = policy
        self.on_drop = on_drop
        self.clients: dict[Any, _Client] = {}
        self.dropped = 0
        self.coalesced = 0
        self._retiring: set[asyncio.Task[Any]] = set()

    def offer(self, socket: Any, message: dict[str, Any]) -> None:
        client = self.clients.get(socket)
        if client is None:
            client = _Client(socket)
            self.clients[socket] = client
            client.sender = asyncio.create_task(self._send(client), name="pi-browser-sender")
        key = str(message.get("id") or id(message))
        if key in client.pending:
            self.coalesced += 1
        elif len(client.pending) >= self.policy.websocket_pending:
            self.remove(socket)
            self.dropped += 1
            self._close_dropped_socket(socket)
            return
        client.pending[key] = message
        client.wakeup.set()

    def broadcast(self, clients: set[Any], message: dict[str, Any]) -> None:
        for socket in tuple(self.clients):
            if socket not in clients:
                self.remove(socket)
        for socket in tuple(clients):
            self.offer(socket, message)

    def remove(self, socket: Any) -> None:
        client = self.clients.pop(socket, None)
        self.on_drop(socket)
        if client and client.sender and client.sender is not asyncio.current_task():
            client.sender.cancel()
            self._retiring.add(client.sender)
            client.sender.add_done_callback(self._retiring.discard)

    def _close_dropped_socket(self, socket: Any) -> None:
        close = getattr(socket, "close", None)
        if close is None:
            return
        async def disconnect() -> None:
            with contextlib.suppress(Exception):
                async with asyncio.timeout(self.policy.websocket_seconds):
                    await close(code=1013)
        task = asyncio.create_task(disconnect(), name="pi-browser-close")
        self._retiring.add(task)
        task.add_done_callback(self._retiring.discard)

    async def _send(self, client: _Client) -> None:
        try:
            while True:
                await client.wakeup.wait()
                client.wakeup.clear()
                while client.pending:
                    _, message = client.pending.popitem(last=False)
                    async with asyncio.timeout(self.policy.websocket_seconds):
                        await client.socket.send_json(message)
        except asyncio.CancelledError:
            raise
        except Exception:
            self.dropped += 1
            self._close_dropped_socket(client.socket)
        finally:
            if self.clients.get(client.socket) is client:
                self.clients.pop(client.socket, None)
                self.on_drop(client.socket)

    async def close(self) -> None:
        tasks = {c.sender for c in self.clients.values() if c.sender is not None}
        tasks.update(self._retiring)
        for socket in tuple(self.clients):
            self.remove(socket)
        await bounded_wait(tasks, self.policy.cancel_seconds)


def _posix_group_has_live_members(pgid: int) -> bool:
    """Conservative liveness check; zombies cannot execute or write artifacts."""
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    if not sys.platform.startswith("linux"):
        return True
    # Linux killpg(0) also reports unreaped zombies. Only ignore a group if
    # /proc positively shows zombie members and no live/unknown member.
    zombie_found = False
    unknown = False
    try:
        with os.scandir("/proc") as entries:
            for entry in entries:
                if not entry.name.isdigit():
                    continue
                try:
                    data = Path(entry.path, "stat").read_text()
                    stat = data.rsplit(")", 1)[1].split()
                    if int(stat[2]) == pgid:
                        if stat[0] not in {"Z", "X"}:
                            return True
                        zombie_found = True
                except FileNotFoundError:
                    continue
                except (OSError, ValueError, IndexError):
                    unknown = True
    except OSError:
        return True
    if zombie_found and not unknown:
        return False
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    return True


async def stop_remaining_process_group(pgid: int, seconds: float) -> None:
    """Fence leftover POSIX children after the wrapper's graceful termination.

    Process groups are not a sandbox: a process that deliberately creates a new
    session can escape. Windows continues to use the existing Job Object path.
    """
    if os.name == "nt":
        raise RuntimeError("POSIX process group cleanup used on Windows")
    if pgid <= 1 or pgid == os.getpgrp():
        raise ValueError("Refusing to signal our own or an invalid process group")
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + seconds
    while await asyncio.to_thread(_posix_group_has_live_members, pgid):
        if time.monotonic() >= deadline:
            raise RuntimeError("POSIX process group cleanup is unconfirmed; resume is blocked")
        await asyncio.sleep(0.01)
