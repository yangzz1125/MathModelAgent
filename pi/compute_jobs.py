"""Host-owned bounded processes. Not an OS/filesystem security sandbox."""
from __future__ import annotations
import asyncio
import contextlib
import hashlib
import math
import os
import signal
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


class HostCleanupError(RuntimeError):
    """Retain native ownership until the Host can confirm cleanup."""
    def __init__(self, process, job, cause):
        super().__init__('Host process cleanup unconfirmed: ' + str(cause))
        self.process = process
        self.job = job


@dataclass
class JobResult:
    argv: list[str]
    returncode: int
    elapsed_seconds: float
    output: str
    timed_out: bool = False
    output_truncated: bool = False

    def record(self) -> dict[str, Any]:
        return asdict(self)


def safe_path(root: Path, relative: str, *, prefix: str | None = None) -> Path:
    if not isinstance(relative, str) or not relative or '\\' in relative or '\x00' in relative:
        raise ValueError('Expected a workspace-relative POSIX path')
    rel = Path(relative)
    if rel.is_absolute() or ':' in relative or '..' in rel.parts:
        raise ValueError('Path escapes workspace')
    root = root.resolve()
    target = root / rel
    for node in (target, *target.parents):
        if node == root:
            break
        if node.is_symlink():
            raise ValueError('Symlinks are not permitted in evidence paths')
    if not target.resolve().is_relative_to(root):
        raise ValueError('Path escapes workspace')
    if prefix and not target.resolve().is_relative_to((root / prefix).resolve()):
        raise ValueError('Path outside permitted stage directory')
    return target


def hashes(root: Path, directories: tuple[str, ...] | list[str]) -> dict[str, str]:
    root = root.resolve()
    result: dict[str, str] = {}
    for relative in directories:
        base = safe_path(root, relative)
        for path in sorted(base.rglob('*')) if base.is_dir() else [base]:
            if path.is_symlink():
                raise ValueError('artifact_changed: symlink in evidence tree')
            if not path.is_file():
                continue
            checked = safe_path(root, path.relative_to(root).as_posix())
            digest = hashlib.sha256()
            with checked.open('rb') as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b''):
                    digest.update(chunk)
            result[path.relative_to(root).as_posix()] = digest.hexdigest()
    return result


def unchanged(root: Path, expected: dict[str, str]) -> bool:
    return hashes(root, list(expected)) == expected


async def _stop_tree(process: asyncio.subprocess.Process, job=None) -> None:
    if job is not None:
        assigned = job.job_assigned
        job.terminate_job()
        if not assigned and process.returncode is None:
            process.kill()  # Assignment failed while the child was suspended.
    elif os.name == 'nt':
        if process.returncode is None:
            killer = await asyncio.create_subprocess_exec(
                'taskkill', '/PID', str(process.pid), '/T', '/F',
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            try:
                await asyncio.wait_for(killer.wait(), 5)
                if killer.returncode and process.returncode is None:
                    raise RuntimeError('Host process-tree cleanup unconfirmed')
            except asyncio.TimeoutError:
                killer.kill()
                await asyncio.wait_for(killer.wait(), 2)
                raise RuntimeError('Host process-tree cleanup unconfirmed')
    else:
        # Even a successfully exited leader may leave children holding stdout.
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
    if process.returncode is None:
        await asyncio.wait_for(process.wait(), 5)


async def _cleanup_job(process, job):
    try:
        await _stop_tree(process, job)
    except Exception as exc:
        raise HostCleanupError(process, job, exc) from exc


async def run_job(argv: list[str], cwd: Path, *, seconds: float,
                  max_output: int = 16000, env: dict[str, str] | None = None) -> JobResult:
    if not argv or not all(isinstance(x, str) and x for x in argv):
        raise ValueError('Nonempty argv required')
    if isinstance(seconds, bool) or not math.isfinite(seconds) or not 0 < seconds <= 3600 or max_output < 1:
        raise ValueError('Invalid job budget')
    started = time.monotonic()
    environment = {**os.environ, 'PYTHONUNBUFFERED': '1', 'MPLBACKEND': 'Agg',
                   'PYTHONDONTWRITEBYTECODE': '1', **(env or {})}
    for key in list(environment):
        if any(token in key.upper() for token in ('API_KEY', 'AUTH_TOKEN', 'SECRET', 'TOOL_POLICY_TOKEN')):
            environment.pop(key, None)
    process = await asyncio.create_subprocess_exec(
        *argv, cwd=cwd, env=environment, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT, stdin=asyncio.subprocess.DEVNULL,
        start_new_session=(os.name != 'nt'),
        creationflags=(0x08000000 | 0x00000004) if os.name == 'nt' else 0)
    job = None
    output = bytearray()
    total = 0
    async def drain():
        nonlocal total
        while block := await process.stdout.read(8192):
            total += len(block)
            output.extend(block)
            if len(output) > max_output:
                del output[:-max_output]
    reader = asyncio.create_task(drain())
    timed_out = False
    try:
        if os.name == 'nt':
            from pi.windows_host import WindowsHostBoundary
            job = WindowsHostBoundary('host-compute', cwd)
            job.assign_and_resume(process.pid)
        async with asyncio.timeout(max(.001, seconds - (time.monotonic() - started))):
            await process.wait()
            await reader
    except TimeoutError:
        timed_out = True
    finally:
        cleanup = asyncio.create_task(_cleanup_job(process, job))
        interrupted = False
        try:
            # Repeated pause/cancel requests must not detach native cleanup.
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    interrupted = True
            cleanup.result()
            if interrupted:
                raise asyncio.CancelledError
        finally:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
    return JobResult(argv, process.returncode if process.returncode is not None else -1,
                     round(time.monotonic() - started, 4), output.decode('utf-8', errors='replace'),
                     timed_out, total > max_output)
