"""Thin FastAPI bridge between the MathModelAgent Vue UI and Pi RPC mode."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import time
import uuid
import zipfile
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.websockets import WebSocketDisconnect

from pi.figure_quality import figure_stack_errors
from pi.scientific_review import (
    ScientificContractError,
    acceptance_chain_errors,
    merge_plan_revision,
    paper_plan_frozen_errors,
    paper_source_errors,
    parse_review,
    validate_paper_manifest,
    validate_paper_plan,
)
from pi.staged_workflow import (
    ContractError,
    artifact_hashes,
    expand_problem_phases,
    final_repair_prompt,
    final_stage_prompt,
    frozen_errors,
    initial_workflow,
    method_replan_prompt,
    paper_manifest_repair_prompt,
    paper_plan_repair_prompt,
    paper_planning_prompt,
    plan_audit_prompt,
    plan_revision_prompt,
    planning_prompt,
    problem_prompt,
    repair_prompt,
    result_errors,
    review_prompt,
    scientific_repair_prompt,
    scientific_review_prompt,
    stage_scope_errors,
    validate_execution_plan,
    workspace_hashes,
    writing_repair_prompt,
)

ROOT = Path(__file__).resolve().parents[1]
WORKSPACES = ROOT / "workspaces"
UPSTREAM_SKILLS = ROOT / "skills"
PI_SKILLS = ROOT / "pi" / "skills"
ENTRY_SKILL = PI_SKILLS / "mathmodelagent-pi" / "SKILL.md"
VENV_SCRIPTS = ROOT / ".venv-pi" / "Scripts"
TASK_ID_RE = re.compile(r"^[0-9a-f]{12}$")
MAX_UPLOAD_BYTES = 100 * 1024 * 1024
MAX_PROJECT_BYTES = 500 * 1024 * 1024
RPC_STREAM_LIMIT_BYTES = 64 * 1024 * 1024
MAX_VERIFY_REPAIRS = 2
MODEL_ID_RE = re.compile(r"^[A-Za-z0-9._:/-]{1,200}$")
THINKING_LEVELS = ("off", "minimal", "low", "medium", "high", "xhigh", "max")
SCAFFOLD_DIRS = ("reports", "code", "results", "figures", "paper")
PROBLEM_SUFFIXES = {".pdf", ".md", ".txt", ".docx"}

PHASES = (
    ("analysis", "赛题分析与建模", "reports/ANALYSIS_MODELING_REPORT.md"),
    ("coding", "编程求解与图表", "reports/RESULTS_REPORT.md"),
    ("diagram", "流程与架构图", "reports/DRAWIO_REPORT.md"),
    ("writing", "竞赛论文撰写", "paper"),
    ("verify", "验证和验收", "reports/VERIFY_REPORT.md"),
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pid_exists(pid: Any) -> bool:
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def _message(msg_type: str, content: str = "", **extra: Any) -> dict[str, Any]:
    return {
        "id": uuid.uuid4().hex,
        "created_at": _now(),
        "msg_type": msg_type,
        "content": content,
        **extra,
    }


def _task_workspace(task_id: str, *, must_exist: bool = True) -> Path:
    if not TASK_ID_RE.fullmatch(task_id):
        raise HTTPException(status_code=400, detail="Invalid task id")
    workspace = WORKSPACES / task_id
    if must_exist and not workspace.is_dir():
        raise HTTPException(status_code=404, detail="Task not found")
    return workspace


def _safe_file(workspace: Path, relative: str) -> Path:
    try:
        path = (workspace / relative).resolve()
        path.relative_to(workspace.resolve())
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Invalid file path") from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return path


def _visible_files(workspace: Path) -> list[dict[str, Any]]:
    files = []
    for path in sorted(workspace.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(workspace)
        if any(part.startswith(".pi") or part == "__pycache__" for part in relative.parts):
            continue
        stat = path.stat()
        files.append(
            {
                "filename": relative.as_posix(),
                "file_type": path.suffix.lstrip(".").lower(),
                "size": stat.st_size,
                "modified_time": datetime.fromtimestamp(
                    stat.st_mtime, timezone.utc
                ).isoformat(),
            }
        )
    return files


def _paper_pdf(workspace: Path) -> Path | None:
    preferred = workspace / "paper" / "main.pdf"
    if preferred.is_file() and preferred.stat().st_size:
        return preferred
    for path in sorted((workspace / "paper").glob("*.pdf")):
        if path.stat().st_size:
            return path
    return None


def _paper_readable(workspace: Path) -> bool:
    pdf = _paper_pdf(workspace)
    converter = shutil.which("pdftoppm")
    if not pdf or not converter:
        return False
    output_dir = workspace / ".pi-bridge"
    output_dir.mkdir(exist_ok=True)
    prefix = output_dir / "paper-check"
    try:
        completed = subprocess.run(
            [
                converter,
                "-f",
                "1",
                "-l",
                "1",
                "-singlefile",
                "-scale-to",
                "32",
                "-png",
                str(pdf),
                str(prefix),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        image = prefix.with_suffix(".png")
        readable = completed.returncode == 0 and image.is_file() and image.stat().st_size > 0
        image.unlink(missing_ok=True)
        return readable
    except (OSError, subprocess.SubprocessError):
        return False


def _verification_passed(text: str) -> bool:
    return bool(
        re.search(
            r"(?im)^\s*(?:#+\s*)?(?:(?:final\s+)?(?:status|result|conclusion)|(?:最终)?(?:验收)?结论)?\s*[:：-]?\s*PASS\s*[.!。]?\s*$",
            text,
        )
    )


def _phase_statuses(workspace: Path, task_status: str) -> list[dict[str, str]]:
    completed: set[str] = set()
    todo = workspace / "todo.md"
    if todo.is_file():
        text = todo.read_text(encoding="utf-8", errors="replace")
        for index, (phase_id, _, _) in enumerate(PHASES, start=1):
            if re.search(rf"-\s*\[[xX]\]\s*{index}\.", text):
                completed.add(phase_id)

    for phase_id, _, marker in PHASES:
        path = workspace / marker
        if path.is_file() or (path.is_dir() and any(path.iterdir())):
            completed.add(phase_id)

    first_pending = next(
        (phase_id for phase_id, _, _ in PHASES if phase_id not in completed), None
    )
    return [
        {
            "id": phase_id,
            "label": label,
            "status": (
                "completed"
                if phase_id in completed
                else "running"
                if phase_id == first_pending and task_status in {"starting", "running"}
                else "pending"
            ),
        }
        for phase_id, label, _ in PHASES
    ]


def _content_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return ""
    parts = []
    for item in value:
        if not isinstance(item, dict):
            continue
        text = item.get("text") or item.get("thinking")
        if isinstance(text, str):
            parts.append(text)
    return "\n".join(parts)


def _visible_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return ""
    return "\n".join(
        str(item.get("text"))
        for item in value
        if isinstance(item, dict)
        and item.get("type") == "text"
        and isinstance(item.get("text"), str)
    )


def _result_text(result: Any) -> str:
    if not isinstance(result, dict):
        return ""
    return _content_text(result.get("content"))[:100_000]


@lru_cache(maxsize=1)
def _available_models() -> tuple[dict[str, Any], ...]:
    """Read the local Pi model catalog without accessing credentials."""
    pi_executable = shutil.which("pi.cmd") or shutil.which("pi")
    if not pi_executable:
        return ()
    try:
        completed = subprocess.run(
            [pi_executable, "--list-models"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except (OSError, subprocess.SubprocessError):
        return ()

    models = []
    for line in completed.stdout.splitlines()[1:]:
        columns = line.split()
        if len(columns) < 6:
            continue
        provider, model_id, context, max_output, thinking, images = columns[:6]
        full_id = f"{provider}/{model_id}"
        if not MODEL_ID_RE.fullmatch(full_id):
            continue
        models.append(
            {
                "id": full_id,
                "provider": provider,
                "model": model_id,
                "context": context,
                "max_output": max_output,
                "thinking": thinking == "yes",
                "images": images == "yes",
            }
        )
    return tuple(models)


def _task_model_config(model: str, thinking: str) -> tuple[str, str]:
    model = model.strip() or os.environ.get("MATHMODEL_PI_MODEL", "").strip()
    thinking = thinking.strip().lower() or os.environ.get(
        "MATHMODEL_PI_THINKING", "high"
    ).strip().lower()
    if model and not MODEL_ID_RE.fullmatch(model):
        raise HTTPException(status_code=400, detail="Invalid model id")
    available = _available_models()
    if model and available and not any(item["id"] == model for item in available):
        raise HTTPException(status_code=400, detail="Model is not available in Pi")
    if thinking not in THINKING_LEVELS:
        raise HTTPException(status_code=400, detail="Invalid thinking level")
    return model, thinking


def _document_stack_errors(paper_engine: str) -> list[str]:
    engine = paper_engine.strip().lower()
    command = "xelatex" if engine == "latex" else "typst"
    errors = []
    if not shutil.which(command):
        errors.append(f"document_preflight: missing {command} for {paper_engine}")
    if not shutil.which("pdftoppm"):
        errors.append("document_preflight: missing pdftoppm for visual verification")
    return errors


def _upload_path(raw_path: str, source_folder: str) -> Path:
    """Return a safe path relative to the generated input directory."""
    normalized = raw_path.replace("\\", "/")
    path = PurePosixPath(normalized)
    windows_path = PureWindowsPath(raw_path)
    parts = list(path.parts)
    if source_folder and parts and parts[0] == source_folder:
        parts = parts[1:]
    unsafe = (
        not parts
        or "\x00" in raw_path
        or path.is_absolute()
        or bool(windows_path.drive or windows_path.root)
        or any(part in {"", ".", ".."} or ":" in part for part in parts)
    )
    if unsafe:
        raise HTTPException(status_code=400, detail=f"Invalid upload path: {raw_path}")
    return Path(*parts)


def _detect_problem(
    files: list[dict[str, Any]], source_folder: str
) -> tuple[str, list[str]]:
    candidates = [
        str(item["path"])
        for item in files
        if Path(str(item["path"])).suffix.lower() in PROBLEM_SUFFIXES
    ]
    root_candidates = [path for path in candidates if "/" not in path]
    folder_stem = source_folder.removesuffix("题").casefold()
    named = [
        path
        for path in root_candidates
        if Path(path).stem.removesuffix("题").casefold() == folder_stem
    ]
    problem = (
        named[0]
        if len(named) == 1
        else root_candidates[0]
        if len(root_candidates) == 1
        else candidates[0]
        if len(candidates) == 1
        else ""
    )
    return (f"input/{problem}" if problem else "", [f"input/{p}" for p in candidates])


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


async def _initialize_project(
    *,
    question: str,
    source_folder: str,
    files: list[UploadFile],
    relative_paths: list[str],
) -> tuple["TaskRuntime", dict[str, Any]]:
    task_id = uuid.uuid4().hex[:12]
    workspace = _task_workspace(task_id, must_exist=False)
    input_dir = workspace / "input"
    input_dir.mkdir(parents=True)
    for directory in SCAFFOLD_DIRS:
        (workspace / directory).mkdir()

    copied: list[dict[str, Any]] = []
    total_bytes = 0
    seen_paths: set[str] = set()
    if relative_paths and len(relative_paths) != len(files):
        shutil.rmtree(workspace, ignore_errors=True)
        raise HTTPException(status_code=400, detail="File path list does not match uploads")
    try:
        for index, upload in enumerate(files):
            raw_path = (
                relative_paths[index]
                if index < len(relative_paths)
                else Path(upload.filename or "").name
            )
            relative = _upload_path(raw_path, source_folder)
            manifest_path = relative.as_posix()
            if manifest_path in seen_paths:
                raise HTTPException(
                    status_code=400, detail=f"Duplicate upload path: {manifest_path}"
                )
            seen_paths.add(manifest_path)
            target = input_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            file_bytes = 0
            with target.open("wb") as output:
                while chunk := await upload.read(1024 * 1024):
                    file_bytes += len(chunk)
                    total_bytes += len(chunk)
                    if file_bytes > MAX_UPLOAD_BYTES or total_bytes > MAX_PROJECT_BYTES:
                        raise HTTPException(
                            status_code=413, detail="Project upload is too large"
                        )
                    output.write(chunk)
            copied.append(
                {
                    "path": manifest_path,
                    "size_bytes": file_bytes,
                    "type": target.suffix.lstrip(".").lower(),
                }
            )

        if question.strip():
            notes_name = "problem.md" if not copied else "user_notes.md"
            notes = input_dir / notes_name
            notes.write_text(question.strip() + "\n", encoding="utf-8")
            copied.append(
                {
                    "path": notes_name,
                    "size_bytes": notes.stat().st_size,
                    "type": "md",
                }
            )
        if not copied:
            raise HTTPException(
                status_code=400, detail="Problem text or a project folder is required"
            )

        problem_file, candidates = _detect_problem(copied, source_folder)
        if not problem_file and question.strip():
            problem_file = "input/problem.md"
        datasets = [
            f"input/{item['path']}"
            for item in copied
            if f"input/{item['path']}" != problem_file
            and Path(str(item["path"])).suffix.lower() not in PROBLEM_SUFFIXES
        ]
        references = [
            f"input/{item['path']}"
            for item in copied
            if f"input/{item['path']}" != problem_file
            and Path(str(item["path"])).suffix.lower() in PROBLEM_SUFFIXES
        ]
        manifest = {
            "schema_version": 1,
            "problem_file": problem_file,
            "problem_candidates": candidates,
            "datasets": datasets,
            "references": references,
            "files": copied,
        }
        project = {
            "schema_version": 1,
            "project_id": task_id,
            "status": "ready",
            "created_at": _now(),
            "source_folder": source_folder,
            "problem_file": problem_file,
        }
        _write_json(workspace / "input_manifest.json", manifest)
        _write_json(workspace / "project.json", project)
        (workspace / "todo.md").write_text(
            "# 工作流\n\n"
            "- [ ] 1. 赛题分析与建模\n"
            "- [ ] 2. 编程求解与图表\n"
            "- [ ] 3. 流程与架构图\n"
            "- [ ] 4. 竞赛论文撰写\n"
            "- [ ] 5. 验证和验收\n",
            encoding="utf-8",
        )
    except Exception:
        shutil.rmtree(workspace, ignore_errors=True)
        raise

    runtime = TaskRuntime(task_id=task_id, workspace=workspace, status="ready")
    TASKS[task_id] = runtime
    await runtime.system("项目工作区初始化完成")
    summary = {
        "project_id": task_id,
        "status": "ready",
        "workspace": str(workspace),
        "source_folder": source_folder,
        "problem_file": problem_file,
        "problem_candidates": candidates,
        "datasets": datasets,
        "references": references,
        "file_count": len(copied),
        "total_bytes": total_bytes,
    }
    return runtime, summary


class StartProjectRequest(BaseModel):
    """Configuration frozen when a ready project starts."""

    question: str = ""
    problem_file: str = ""
    competition: str = "CUMCM"
    language: str = "Chinese"
    paper_engine: str = "LaTeX"
    model: str = ""
    thinking: str = "high"
    planner_model: str = ""
    planner_thinking: str = "high"
    worker_model: str = ""
    worker_thinking: str = "high"


async def _start_project(
    runtime: "TaskRuntime", request: StartProjectRequest
) -> None:
    if runtime.status != "ready":
        raise HTTPException(status_code=409, detail="Project is not ready to start")
    paper_engine = request.paper_engine.strip().lower()
    if paper_engine not in {"latex", "typst"}:
        raise HTTPException(status_code=400, detail="Unsupported paper engine")
    figure_errors = figure_stack_errors(request.language)
    environment_errors = _document_stack_errors(request.paper_engine)
    if figure_errors or environment_errors:
        raise HTTPException(
            status_code=503, detail="; ".join(figure_errors + environment_errors)
        )
    legacy_model = request.model.strip()
    planner_model, planner_thinking = _task_model_config(
        request.planner_model or legacy_model or "openai/gpt-5.6-sol",
        request.planner_thinking or request.thinking,
    )
    worker_model, worker_thinking = _task_model_config(
        request.worker_model or legacy_model or "openai/gpt-5.6-luna",
        request.worker_thinking or request.thinking,
    )
    manifest_path = runtime.workspace / "input_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    problem_file = request.problem_file or str(manifest.get("problem_file") or "")
    if not problem_file:
        raise HTTPException(status_code=400, detail="Select the main problem file")
    problem_path = _safe_file(runtime.workspace, problem_file)
    try:
        problem_path.relative_to((runtime.workspace / "input").resolve())
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail="Problem file must be inside input"
        ) from exc

    manifest["problem_file"] = problem_file
    _write_json(manifest_path, manifest)
    project_path = runtime.workspace / "project.json"
    project = json.loads(project_path.read_text(encoding="utf-8"))
    runtime.requested_model = planner_model
    runtime.thinking_level = planner_thinking
    runtime.model = planner_model or "Pi default"
    runtime.planner_model = planner_model
    runtime.planner_thinking = planner_thinking
    runtime.worker_model = worker_model
    runtime.worker_thinking = worker_thinking
    runtime.started_at = _now()
    workflow = initial_workflow(
        {"model": planner_model, "thinking": planner_thinking},
        {"model": worker_model, "thinking": worker_thinking},
        contract_version=2,
    )
    workflow["phases"][0]["started_at"] = runtime.started_at
    workflow["stage_snapshot"] = workspace_hashes(runtime.workspace)
    project.update(
        {
            "status": "starting",
            "problem_file": problem_file,
            "competition": request.competition,
            "language": request.language,
            "paper_engine": request.paper_engine,
            "model": planner_model,
            "thinking": planner_thinking,
            "planner_model": planner_model,
            "planner_thinking": planner_thinking,
            "worker_model": worker_model,
            "worker_thinking": worker_thinking,
            "started_at": runtime.started_at,
            "workflow": workflow,
        }
    )
    _write_json(project_path, project)
    runtime.set_status("starting")
    await runtime.publish(
        _message("user", request.question.strip() or f"使用 {problem_file} 开始完整建模")
    )
    prompt = planning_prompt(
        problem_file=problem_file,
        competition=request.competition,
        language=request.language,
        paper_engine=request.paper_engine,
        notes=request.question.strip(),
    )
    runtime.runner = asyncio.create_task(runtime.run(prompt))


@dataclass
class TaskRuntime:
    """One persistent Pi RPC process and its browser-facing state."""

    task_id: str
    workspace: Path
    messages: list[dict[str, Any]] = field(default_factory=list)
    clients: set[WebSocket] = field(default_factory=set)
    status: str = "starting"
    process: asyncio.subprocess.Process | None = None
    runner: asyncio.Task[None] | None = None
    started_at: str = field(default_factory=_now)
    requested_model: str = ""
    thinking_level: str = "high"
    model: str = "Pi default"
    planner_model: str = ""
    planner_thinking: str = "high"
    worker_model: str = ""
    worker_thinking: str = "high"
    _assistant_id: str | None = None
    _assistant_text: str = ""
    _last_assistant_text: str = ""
    _tool_message_ids: dict[str, str] = field(default_factory=dict)
    _tool_watchdogs: dict[str, asyncio.Task[None]] = field(default_factory=dict)
    _pending_rpc: dict[str, list[asyncio.Future[dict[str, Any]]]] = field(
        default_factory=dict
    )
    _transition_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _write_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _budget_exceeded: bool = False

    @property
    def message_file(self) -> Path:
        return self.workspace / ".pi-bridge" / "messages.json"

    def set_status(self, status: str) -> None:
        self.status = status
        project_path = self.workspace / "project.json"
        if not project_path.is_file():
            return
        try:
            project = json.loads(project_path.read_text(encoding="utf-8"))
            project["status"] = status
            _write_json(project_path, project)
        except (OSError, json.JSONDecodeError):
            pass

    async def publish(self, message: dict[str, Any], *, persist: bool = True) -> None:
        """Upsert one message, persist stable snapshots, and broadcast it."""
        async with self._write_lock:
            for index, existing in enumerate(self.messages):
                if existing.get("id") == message.get("id"):
                    self.messages[index] = message
                    break
            else:
                self.messages.append(message)
            if persist:
                snapshot = json.dumps(self.messages, ensure_ascii=False, indent=2)
                await asyncio.to_thread(self._write_messages, snapshot)

        stale = []
        for client in tuple(self.clients):
            try:
                await client.send_json(message)
            except Exception:
                stale.append(client)
        for client in stale:
            self.clients.discard(client)

    def _write_messages(self, snapshot: str) -> None:
        self.message_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.message_file.with_name(
            f"{self.message_file.name}.{uuid.uuid4().hex}.tmp"
        )
        temporary.write_text(snapshot, encoding="utf-8")
        try:
            for attempt in range(6):
                try:
                    os.replace(temporary, self.message_file)
                    return
                except PermissionError:
                    if attempt == 5:
                        raise
                    time.sleep(0.05 * (attempt + 1))
        finally:
            temporary.unlink(missing_ok=True)

    async def system(self, content: str, kind: str = "info") -> None:
        await self.publish(_message("system", content, type=kind))

    async def send_rpc(self, payload: dict[str, Any]) -> None:
        if not self.process or not self.process.stdin:
            raise RuntimeError("Pi process is not running")
        self.process.stdin.write(
            (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        )
        await self.process.stdin.drain()

    async def rpc_command(
        self, payload: dict[str, Any], *, timeout: float = 15
    ) -> dict[str, Any]:
        command = str(payload.get("type") or "")
        if not command:
            raise ValueError("RPC command requires a type")
        future: asyncio.Future[dict[str, Any]] = (
            asyncio.get_running_loop().create_future()
        )
        self._pending_rpc.setdefault(command, []).append(future)
        try:
            await self.send_rpc({"id": uuid.uuid4().hex, **payload})
            response = await asyncio.wait_for(future, timeout=timeout)
        except Exception:
            pending = self._pending_rpc.get(command, [])
            if future in pending:
                pending.remove(future)
            raise
        if not response.get("success", False):
            raise RuntimeError(
                f"Pi {command} failed: {response.get('error', 'unknown error')}"
            )
        return response

    async def prompt(self, text: str, *, initial: bool = False) -> None:
        self._last_assistant_text = ""
        payload: dict[str, Any] = {
            "id": uuid.uuid4().hex,
            "type": "prompt",
            "message": text,
        }
        if not initial and self.status == "running":
            payload["streamingBehavior"] = "steer"
        self.set_status("running")
        await self.send_rpc(payload)

    async def terminate(self) -> None:
        """Terminate the RPC wrapper and its Pi/Node descendants."""
        for watchdog in self._tool_watchdogs.values():
            watchdog.cancel()
        self._tool_watchdogs.clear()
        if not self.process or self.process.returncode is not None:
            return
        if os.name == "nt":
            killer = await asyncio.create_subprocess_exec(
                "taskkill",
                "/PID",
                str(self.process.pid),
                "/T",
                "/F",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            await killer.wait()
        else:
            self.process.terminate()
        try:
            await asyncio.wait_for(self.process.wait(), timeout=5)
        except asyncio.TimeoutError:
            self.process.kill()
            await self.process.wait()

    async def pause(self, reason: str = "user") -> None:
        if self.status not in {"starting", "running"}:
            raise HTTPException(status_code=409, detail="Task is not running")
        project = self._project()
        project["status"] = "paused"
        project["paused_at"] = _now()
        project["pause_reason"] = reason
        project["pause_count"] = int(project.get("pause_count") or 0) + 1
        workflow = project.get("workflow")
        if isinstance(workflow, dict):
            phase = self._current_phase(workflow)
            if phase:
                phase["status_before_pause"] = phase.get("status") or "running"
                phase["status"] = "paused"
        self._save_project(project)
        self.status = "paused"
        await self.system("任务已持久化暂停，可以稍后从当前阶段恢复", "warning")
        if self.process and self.process.returncode is None:
            try:
                await self.send_rpc({"type": "clear_queue"})
                await self.send_rpc({"type": "abort"})
            except (BrokenPipeError, ConnectionError, RuntimeError):
                pass
        runner = self.runner
        await self.terminate()
        if runner and runner is not asyncio.current_task() and not runner.done():
            try:
                await asyncio.wait_for(asyncio.shield(runner), timeout=10)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
        self.runner = None
        self.process = None

    def _profile_for_resume(self, project: dict[str, Any]) -> str:
        workflow = project["workflow"]
        stage = str(workflow.get("current") or "")
        mode = str(workflow.get("mode") or "run")
        if stage in {"planning", "plan_audit", "paper_planning", "verify"}:
            return "planner"
        if mode in {"plan_audit", "plan_revision", "scientific_review", "method_replan"}:
            return "planner"
        return "worker"

    def _resume_prompt(self, project: dict[str, Any]) -> str:
        workflow = project["workflow"]
        stage = str(workflow.get("current") or "")
        mode = str(workflow.get("mode") or "run")
        phase = self._current_phase(workflow) or {}
        errors = [str(phase.get("last_error") or "Interrupted by a user pause; recheck current artifacts before continuing.")]
        problem = self._problem(workflow)
        if mode == "plan_audit":
            return plan_audit_prompt()
        if mode == "plan_revision":
            return plan_revision_prompt(workflow.get("last_review") or {})
        if mode == "scientific_review" and problem:
            return scientific_review_prompt(problem)
        if mode == "scientific_repair" and problem:
            return scientific_repair_prompt(problem, workflow.get("last_review") or {})
        if mode == "method_replan" and problem:
            plan = workflow.get("replan_base") or validate_execution_plan(self.workspace)
            ids = [item["id"] for item in plan["problems"]]
            return method_replan_prompt(
                problem["id"],
                workflow.get("last_review") or {},
                ids[ids.index(problem["id"]):],
            )
        if mode == "paper_plan_repair":
            return paper_plan_repair_prompt(errors)
        if mode == "paper_manifest_repair":
            return paper_manifest_repair_prompt(errors)
        if mode == "verify_repair":
            verify = next(
                (item for item in workflow.get("phases", []) if item.get("id") == "verify"),
                {},
            )
            repair_number = int(workflow.get("verify_repair_count") or 1)
            return writing_repair_prompt(
                [str(verify.get("last_error") or errors[0])], repair_number
            )
        if mode in {"candidate_repair", "direct_repair"}:
            return repair_prompt(stage, errors)
        if mode == "final_repair" and problem:
            return final_repair_prompt(problem)
        return self._prompt_for_current(project)

    async def resume(self) -> None:
        if self.status != "paused":
            raise HTTPException(status_code=409, detail="Task is not paused")
        if self.process and self.process.returncode is None:
            raise HTTPException(status_code=409, detail="Task process is still running")
        project = self._project()
        workflow = project.get("workflow")
        if not isinstance(workflow, dict):
            raise HTTPException(status_code=409, detail="Legacy task cannot be resumed persistently")
        profile = self._profile_for_resume(project)
        config = workflow["profiles"][profile]
        self.requested_model = str(config.get("model") or "")
        self.thinking_level = str(config.get("thinking") or "high")
        self.model = self.requested_model or "Pi default"
        project["status"] = "starting"
        project["resumed_at"] = _now()
        project["resume_count"] = int(project.get("resume_count") or 0) + 1
        phase = self._current_phase(workflow)
        if phase:
            phase["status"] = str(phase.pop("status_before_pause", "running"))
        project.pop("pause_reason", None)
        self._save_project(project)
        self.status = "starting"
        await self.system("正在从持久化阶段恢复任务")
        self.runner = asyncio.create_task(self.run(self._resume_prompt(project)))

    async def abort(self) -> None:
        if self.process and self.process.returncode is None:
            await self.send_rpc({"type": "clear_queue"})
            await self.send_rpc({"type": "abort"})
        self.set_status("cancelled")
        await self.system("任务已停止", "warning")
        await self.terminate()

    async def run(self, prompt: str) -> None:
        """Spawn Pi, send the workflow prompt, and translate RPC events."""
        pi_executable = shutil.which("pi.cmd") or shutil.which("pi")
        if not pi_executable:
            self.set_status("failed")
            await self.system("找不到 Pi 可执行文件", "error")
            return

        session_dir = self.workspace / ".pi-sessions"
        session_dir.mkdir(parents=True, exist_ok=True)
        command = [
            pi_executable,
            "--mode",
            "rpc",
            "--skill",
            str(UPSTREAM_SKILLS),
            "--skill",
            str(PI_SKILLS),
            "--append-system-prompt",
            str(ENTRY_SKILL),
            "--session-dir",
            str(session_dir),
            "--name",
            f"MathModelAgent {self.task_id}",
        ]
        model = self.requested_model or os.environ.get("MATHMODEL_PI_MODEL", "").strip()
        if model:
            command.extend(("--model", model))
        thinking = self.thinking_level or os.environ.get(
            "MATHMODEL_PI_THINKING", ""
        ).strip()
        if thinking:
            command.extend(("--thinking", thinking))

        environment = os.environ.copy()
        environment.update(
            {
                "MATHMODELAGENT_ROOT": str(ROOT),
                "VIRTUAL_ENV": str(ROOT / ".venv-pi"),
                "PATH": f"{VENV_SCRIPTS}{os.pathsep}{environment.get('PATH', '')}",
                "MPLBACKEND": "Agg",
                "PYTHONUTF8": "1",
            }
        )
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

        try:
            self.process = await asyncio.create_subprocess_exec(
                *command,
                cwd=self.workspace,
                env=environment,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=RPC_STREAM_LIMIT_BYTES,
                creationflags=creationflags,
            )
            project = self._project()
            project["runtime_owner_pid"] = os.getpid()
            project["pi_pid"] = self.process.pid
            self._save_project(project)
            self.set_status("running")
            await self.system("Pi 已启动，正在执行 MathModelAgent 全流程")
            stderr_task = asyncio.create_task(self._read_stderr())
            await self.send_rpc({"id": "initial-state", "type": "get_state"})
            await self.prompt(prompt, initial=True)
            await self._read_stdout()
            await stderr_task
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            try:
                project = self._project()
                workflow = project.get("workflow")
                if isinstance(workflow, dict) and workflow.get("contract_version") == 2:
                    project["status"] = "paused"
                    project["paused_at"] = _now()
                    project["pause_reason"] = f"bridge_error: {exc}"
                    phase = self._current_phase(workflow)
                    if phase:
                        phase["status_before_pause"] = phase.get("status") or "running"
                        phase["status"] = "paused"
                    self._save_project(project)
                    self.status = "paused"
                    try:
                        await self.system(f"Bridge 异常，任务已自动持久化暂停：{exc}", "warning")
                    except Exception:
                        pass
                else:
                    self.set_status("failed")
                    await self.system(f"Pi bridge 运行失败：{exc}", "error")
            except Exception:
                self.set_status("failed")
        finally:
            await self.terminate()
            if self.status not in {"completed", "cancelled", "failed", "paused"}:
                self.set_status("failed")
                await self.system("Pi 进程意外退出", "error")

    async def _read_stdout(self) -> None:
        assert self.process and self.process.stdout
        while line := await self.process.stdout.readline():
            try:
                event = json.loads(line.decode("utf-8").rstrip("\r\n"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            await self._handle_event(event)

    async def _read_stderr(self) -> None:
        assert self.process and self.process.stderr
        log_path = self.workspace / ".pi-bridge" / "pi.stderr.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("ab") as log:
            while chunk := await self.process.stderr.read(8192):
                log.write(chunk)
                log.flush()

    async def _watch_tool(self, tool_call_id: str, seconds: int) -> None:
        try:
            await asyncio.sleep(seconds)
            if tool_call_id not in self._tool_watchdogs:
                return
            self._budget_exceeded = True
            await self.system(
                f"当前问题的单次命令超过 {seconds} 秒，已中止并进入自动修复",
                "warning",
            )
            await self.send_rpc({"type": "abort"})
        except (asyncio.CancelledError, RuntimeError):
            pass

    async def _handle_event(self, event: dict[str, Any]) -> None:
        event_type = event.get("type")
        if event_type == "response":
            command = str(event.get("command") or "")
            pending = self._pending_rpc.get(command, [])
            if pending:
                future = pending.pop(0)
                if not future.done():
                    future.set_result(event)
            if event.get("id") == "initial-state" and event.get("success"):
                model = (event.get("data") or {}).get("model") or {}
                self.model = str(model.get("name") or model.get("id") or self.model)
            elif not event.get("success", True):
                await self.system(
                    f"Pi 命令失败：{event.get('error', 'unknown error')}", "error"
                )
            return

        if event_type == "message_start":
            message = event.get("message") or {}
            if message.get("role") == "assistant":
                self._assistant_id = uuid.uuid4().hex
                self._assistant_text = ""
            return

        if event_type == "message_update":
            update = event.get("assistantMessageEvent") or {}
            if update.get("type") == "text_delta":
                if not self._assistant_id:
                    self._assistant_id = uuid.uuid4().hex
                self._assistant_text += str(update.get("delta") or "")
                await self.publish(
                    {
                        "id": self._assistant_id,
                        "created_at": _now(),
                        "msg_type": "agent",
                        "agent_type": "PiAgent",
                        "content": self._assistant_text,
                    },
                    persist=False,
                )
            return

        if event_type == "message_end":
            message = event.get("message") or {}
            if message.get("role") == "assistant":
                visible_text = _visible_text(message.get("content"))
                text = _content_text(message.get("content")) or self._assistant_text
                self._last_assistant_text = visible_text
                if text:
                    await self.publish(
                        {
                            "id": self._assistant_id or uuid.uuid4().hex,
                            "created_at": _now(),
                            "msg_type": "agent",
                            "agent_type": "PiAgent",
                            "content": text,
                        }
                    )
                self._assistant_id = None
                self._assistant_text = ""
            return

        if event_type == "tool_execution_start":
            tool_call_id = str(event.get("toolCallId") or uuid.uuid4().hex)
            message_id = uuid.uuid4().hex
            self._tool_message_ids[tool_call_id] = message_id
            args = event.get("args") or {}
            display = args.get("command") if isinstance(args, dict) else None
            if not display:
                display = json.dumps(args, ensure_ascii=False, indent=2)
            await self.publish(
                {
                    "id": message_id,
                    "created_at": _now(),
                    "msg_type": "tool",
                    "tool_name": "execute_code",
                    "tool_label": str(event.get("toolName") or "tool"),
                    "input": {"code": str(display)},
                    "output": None,
                },
                persist=False,
            )
            if str(event.get("toolName") or "") == "bash":
                seconds = self._current_runtime_limit()
                if seconds:
                    self._tool_watchdogs[tool_call_id] = asyncio.create_task(
                        self._watch_tool(tool_call_id, seconds)
                    )
            return

        if event_type in {"tool_execution_update", "tool_execution_end"}:
            tool_call_id = str(event.get("toolCallId") or "")
            message_id = self._tool_message_ids.get(tool_call_id)
            if event_type == "tool_execution_end":
                watchdog = self._tool_watchdogs.pop(tool_call_id, None)
                if watchdog:
                    watchdog.cancel()
            if not message_id:
                return
            result = (
                event.get("partialResult")
                if event_type == "tool_execution_update"
                else event.get("result")
            )
            text = _result_text(result)
            output = None
            if text:
                output = [
                    {
                        "res_type": "error" if event.get("isError") else "stdout",
                        "msg": text,
                        "name": "ToolError",
                        "value": text if event.get("isError") else "",
                        "traceback": "",
                    }
                ]
            existing = next(
                (item for item in self.messages if item.get("id") == message_id), None
            )
            if existing:
                updated = {**existing, "output": output}
                await self.publish(updated, persist=event_type == "tool_execution_end")
            return

        if event_type == "agent_settled":
            asyncio.create_task(self._settled())
            return

        if event_type == "auto_retry_start":
            await self.system(
                f"模型请求重试 {event.get('attempt')}/{event.get('maxAttempts')}"
            )

    def _project(self) -> dict[str, Any]:
        return json.loads((self.workspace / "project.json").read_text(encoding="utf-8"))

    def _save_project(self, project: dict[str, Any]) -> None:
        _write_json(self.workspace / "project.json", project)

    def _current_phase(self, workflow: dict[str, Any]) -> dict[str, Any] | None:
        return next(
            (item for item in workflow.get("phases", []) if item.get("id") == workflow.get("current")),
            None,
        )

    def _problem(self, workflow: dict[str, Any]) -> dict[str, Any] | None:
        current = str(workflow.get("current") or "")
        if not current.startswith("problem:"):
            return None
        try:
            plan = validate_execution_plan(self.workspace)
        except ContractError:
            return None
        problem_id = current.split(":", 1)[1]
        return next((item for item in plan["problems"] if item["id"] == problem_id), None)

    def _current_runtime_limit(self) -> int | None:
        try:
            workflow = self._project().get("workflow") or {}
            problem = self._problem(workflow)
            return int(problem["runtime_limit_seconds"]) if problem else None
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None

    async def _switch_session(self, profile: str) -> None:
        project = self._project()
        workflow = project["workflow"]
        config = workflow["profiles"][profile]
        await self.rpc_command({"type": "new_session"})
        model = str(config.get("model") or "")
        if model:
            provider, model_id = model.split("/", 1)
            await self.rpc_command(
                {"type": "set_model", "provider": provider, "modelId": model_id}
            )
            self.model = model
        thinking = str(config.get("thinking") or "high")
        await self.rpc_command({"type": "set_thinking_level", "level": thinking})
        self.thinking_level = thinking

    def _prompt_for_current(self, project: dict[str, Any]) -> str:
        workflow = project["workflow"]
        stage = str(workflow["current"])
        if stage == "planning":
            return planning_prompt(
                problem_file=str(project["problem_file"]),
                competition=str(project.get("competition") or "CUMCM"),
                language=str(project.get("language") or "Chinese"),
                paper_engine=str(project.get("paper_engine") or "LaTeX"),
                notes="",
            )
        if stage == "plan_audit":
            return plan_audit_prompt()
        if stage == "paper_planning":
            plan = validate_execution_plan(self.workspace)
            return paper_planning_prompt(int(plan.get("plan_version") or 1))
        problem = self._problem(workflow)
        if problem:
            return problem_prompt(problem)
        return final_stage_prompt(
            stage,
            competition=str(project.get("competition") or "CUMCM"),
            language=str(project.get("language") or "Chinese"),
            paper_engine=str(project.get("paper_engine") or "LaTeX"),
        )

    async def _begin_current(self, *, new_session: bool = True) -> None:
        project = self._project()
        workflow = project["workflow"]
        stage = str(workflow["current"])
        profile = (
            "planner"
            if stage in {"planning", "plan_audit", "paper_planning", "verify"}
            else "worker"
        )
        if new_session:
            await self._switch_session(profile)
        project = self._project()
        workflow = project["workflow"]
        phase = self._current_phase(workflow)
        if phase:
            phase["status"] = "running"
            phase["attempts"] = max(1, int(phase.get("attempts") or 0))
            phase["started_at"] = phase.get("started_at") or _now()
        project["status"] = "running"
        self._save_project(project)
        self.status = "running"
        await self.system(f"开始阶段：{phase['label'] if phase else stage}")
        await self.prompt(self._prompt_for_current(project))

    def _gate_current(self, project: dict[str, Any]) -> tuple[list[str], dict[str, Any] | None]:
        workflow = project["workflow"]
        stage = str(workflow["current"])
        errors = frozen_errors(self.workspace, workflow.get("frozen") or {})
        errors.extend(
            stage_scope_errors(
                self.workspace,
                workflow.get("stage_snapshot") or {},
                stage,
            )
        )
        if self._budget_exceeded:
            errors.append("performance_budget: command exceeded runtime_limit_seconds")
            self._budget_exceeded = False
        plan = None
        if stage == "planning":
            report = self.workspace / "reports" / "ANALYSIS_MODELING_REPORT.md"
            if not report.is_file() or not report.stat().st_size:
                errors.append("artifact_missing: reports/ANALYSIS_MODELING_REPORT.md")
            try:
                plan = validate_execution_plan(self.workspace)
            except ContractError as exc:
                errors.append(f"validation_failed: {exc}")
        elif stage.startswith("problem:"):
            problem = self._problem(workflow)
            if not problem:
                errors.append("method_invalid: current problem is absent from execution_plan.json")
            else:
                errors.extend(result_errors(self.workspace, problem))
        elif stage == "paper_planning":
            try:
                active_plan = validate_execution_plan(self.workspace)
                normalized_paper_plan = validate_paper_plan(self.workspace, active_plan)
                errors.extend(
                    paper_plan_frozen_errors(
                        normalized_paper_plan, workflow.get("frozen") or {}
                    )
                )
            except (ContractError, ScientificContractError) as exc:
                errors.append(f"validation_failed: {exc}")
        elif stage == "diagram":
            report = self.workspace / "reports" / "DRAWIO_REPORT.md"
            if not report.is_file() or not report.stat().st_size:
                errors.append("artifact_missing: reports/DRAWIO_REPORT.md")
        elif stage == "writing":
            if not _paper_readable(self.workspace):
                errors.append("validation_failed: paper PDF is missing, empty, or unreadable")
            if workflow.get("contract_version") == 2:
                try:
                    active_plan = validate_execution_plan(self.workspace)
                    paper_plan = validate_paper_plan(self.workspace, active_plan)
                    errors.extend(
                        paper_plan_frozen_errors(
                            paper_plan, workflow.get("frozen") or {}
                        )
                    )
                    validate_paper_manifest(self.workspace, paper_plan)
                    errors.extend(paper_source_errors(self.workspace))
                except (ContractError, ScientificContractError) as exc:
                    errors.append(f"validation_failed: {exc}")
        elif stage == "verify":
            report = self.workspace / "reports" / "VERIFY_REPORT.md"
            text = report.read_text(encoding="utf-8", errors="replace") if report.is_file() else ""
            if workflow.get("contract_version") == 2:
                try:
                    active_plan = validate_execution_plan(self.workspace)
                    errors.extend(acceptance_chain_errors(self.workspace, active_plan))
                except ContractError as exc:
                    errors.append(f"scientific_acceptance: {exc}")
            if not _verification_passed(text):
                errors.append("validation_failed: reports/VERIFY_REPORT.md does not have an explicit PASS conclusion")
            if not _paper_readable(self.workspace):
                errors.append("validation_failed: paper PDF is missing, empty, or unreadable")
        return errors, plan

    async def _wait_with_errors(self, errors: list[str]) -> None:
        project = self._project()
        workflow = project["workflow"]
        phase = self._current_phase(workflow)
        detail = "; ".join(errors)[:2000]
        if workflow.get("contract_version") == 2:
            if phase:
                phase["status"] = "failed"
                phase["last_error"] = detail
            workflow["mode"] = "failed"
            project["status"] = "failed"
            self._save_project(project)
            self.status = "failed"
            await self.system(f"自治工作流失败：{detail}", "error")
            await self.terminate()
            return
        if phase:
            phase["status"] = "waiting"
            phase["last_error"] = detail
        workflow["mode"] = "waiting"
        project["status"] = "waiting"
        self._save_project(project)
        self.status = "waiting"
        await self.system(f"自动修复已停止：{detail}", "warning")

    async def _start_review(self, errors: list[str]) -> None:
        project = self._project()
        workflow = project["workflow"]
        problem = self._problem(workflow)
        if not problem:
            await self._wait_with_errors(errors)
            return
        workflow["mode"] = "review"
        workflow["review_errors"] = errors
        workflow["review_snapshot"] = workspace_hashes(self.workspace)
        self._save_project(project)
        try:
            await self._switch_session("planner")
            await self.system(f"Sol 正在复核 {problem['id']} 的方法和失败证据")
            await self.prompt(review_prompt(problem, errors))
        except Exception as exc:
            await self._wait_with_errors([f"rpc_error: reviewer session failed: {exc}"])

    async def _finish_review(self, project: dict[str, Any]) -> None:
        workflow = project["workflow"]
        problem = self._problem(workflow)
        if not problem:
            await self._wait_with_errors(["method_invalid: review problem missing"])
            return
        before = workflow.get("review_snapshot") or {}
        if workspace_hashes(self.workspace) != before:
            await self._wait_with_errors(["artifact_changed: reviewer modified workspace files"])
            return
        review = self._last_assistant_text.strip()
        if not review:
            await self._wait_with_errors(["rpc_error: reviewer returned no diagnosis"])
            return
        report = self.workspace / "reports" / f"{problem['id']}_REPAIR_REVIEW.md"
        report.write_text(review + "\n", encoding="utf-8")
        if "METHOD_INVALID" in review:
            await self._wait_with_errors(["method_invalid: reviewer requires global replanning"])
            return
        phase = self._current_phase(workflow)
        if phase:
            phase["attempts"] = 3
        workflow["mode"] = "final_repair"
        workflow.pop("review_snapshot", None)
        self._save_project(project)
        try:
            await self._switch_session("worker")
            await self.system(f"Luna 正在执行 {problem['id']} 的最终修复")
            await self.prompt(final_repair_prompt(problem))
        except Exception as exc:
            await self._wait_with_errors([f"rpc_error: final repair session failed: {exc}"])

    async def _start_writing_repair(self, errors: list[str]) -> None:
        project = self._project()
        workflow = project["workflow"]
        repairs_done = int(workflow.get("verify_repair_count") or 0)
        if repairs_done >= MAX_VERIFY_REPAIRS:
            await self._wait_with_errors(errors)
            return
        writing = next(
            (item for item in workflow["phases"] if item.get("id") == "writing"),
            None,
        )
        verify = self._current_phase(workflow)
        if not writing or not verify:
            await self._wait_with_errors(["method_invalid: writing/verify phase missing"])
            return
        repair_number = repairs_done + 1
        workflow["verify_repair_count"] = repair_number
        workflow["current"] = "writing"
        workflow["mode"] = "verify_repair"
        workflow["stage_snapshot"] = workspace_hashes(self.workspace)
        verify["status"] = "pending"
        verify["last_error"] = "; ".join(errors)[:2000]
        writing["status"] = "running"
        writing["attempts"] = int(writing.get("attempts") or 1) + 1
        writing["started_at"] = _now()
        project["status"] = "running"
        self._save_project(project)
        try:
            await self._switch_session("worker")
            await self.system(
                f"验收未通过，Luna 正在执行论文修复 {repair_number}/{MAX_VERIFY_REPAIRS}",
                "warning",
            )
            await self.prompt(writing_repair_prompt(errors, repair_number))
        except Exception as exc:
            await self._wait_with_errors([f"rpc_error: writing repair failed: {exc}"])

    async def _repair_current(self, errors: list[str]) -> None:
        project = self._project()
        workflow = project["workflow"]
        phase = self._current_phase(workflow)
        attempts = int(phase.get("attempts") or 1) if phase else 1
        stage = str(workflow["current"])
        if phase:
            phase["last_error"] = "; ".join(errors)[:2000]

        if stage == "verify":
            await self._start_writing_repair(errors)
            return
        if workflow.get("contract_version") == 2 and stage == "writing":
            if attempts >= 3:
                await self._wait_with_errors(errors)
                return
            phase["attempts"] = attempts + 1
            workflow["mode"] = "paper_manifest_repair"
            self._save_project(project)
            await self.system("论文内容或 manifest 门禁未通过，Luna 正在修复", "warning")
            await self.prompt(paper_manifest_repair_prompt(errors))
            return
        if stage.startswith("problem:"):
            if attempts == 1:
                phase["attempts"] = 2
                workflow["mode"] = "direct_repair"
                self._save_project(project)
                await self.system("确定性门禁未通过，Luna 正在直接修复", "warning")
                await self.prompt(repair_prompt(stage, errors))
                return
            if attempts == 2:
                self._save_project(project)
                await self._start_review(errors)
                return
        elif attempts == 1:
            phase["attempts"] = 2
            workflow["mode"] = "direct_repair"
            self._save_project(project)
            await self.system("阶段门禁未通过，正在自动修复", "warning")
            await self.prompt(repair_prompt(stage, errors))
            return
        await self._wait_with_errors(errors)

    async def _complete_current(
        self, project: dict[str, Any], plan: dict[str, Any] | None
    ) -> None:
        workflow = project["workflow"]
        phase = self._current_phase(workflow)
        stage = str(workflow["current"])
        if phase:
            phase["status"] = "completed"
            phase["completed_at"] = _now()
            phase["last_error"] = ""
        if stage == "planning":
            assert plan is not None
            _write_json(self.workspace / "execution_plan.json", plan)
            expand_problem_phases(workflow, plan)
        elif stage.startswith("problem:"):
            problem_id = stage.split(":", 1)[1]
            workflow.setdefault("frozen", {})[problem_id] = artifact_hashes(
                self.workspace, problem_id
            )
        phases = workflow["phases"]
        current_index = next(index for index, item in enumerate(phases) if item["id"] == stage)
        next_phase = phases[current_index + 1] if current_index + 1 < len(phases) else None
        workflow["mode"] = "run"
        workflow["stage_snapshot"] = workspace_hashes(self.workspace)
        if not next_phase:
            project["status"] = "completed"
            self._save_project(project)
            self.status = "completed"
            await self.system("完整工作流已通过验证，论文 PDF 可以下载", "success")
            await self.terminate()
            return
        workflow["current"] = next_phase["id"]
        next_phase["status"] = "running"
        next_phase["attempts"] = int(next_phase.get("attempts") or 0) + 1
        next_phase["started_at"] = _now()
        project["status"] = "running"
        self._save_project(project)
        try:
            await self._begin_current()
        except Exception as exc:
            await self._wait_with_errors([f"rpc_error: stage switch failed: {exc}"])

    async def _start_plan_audit(
        self, project: dict[str, Any], plan: dict[str, Any]
    ) -> None:
        workflow = project["workflow"]
        planning = next(item for item in workflow["phases"] if item["id"] == "planning")
        audit = next(item for item in workflow["phases"] if item["id"] == "plan_audit")
        planning["status"] = "completed"
        planning["completed_at"] = _now()
        workflow["plan_version"] = int(plan.get("plan_version") or 1)
        workflow["current"] = "plan_audit"
        workflow["mode"] = "plan_audit"
        workflow["review_snapshot"] = workspace_hashes(self.workspace)
        workflow["stage_snapshot"] = workflow["review_snapshot"]
        audit["status"] = "running"
        audit["attempts"] = int(audit.get("attempts") or 0) + 1
        audit["started_at"] = _now()
        project["status"] = "running"
        self._save_project(project)
        try:
            await self._switch_session("planner")
            await self.system("Sol 正在独立审查执行计划")
            await self.prompt(plan_audit_prompt())
        except Exception as exc:
            await self._wait_with_errors([f"rpc_error: plan audit failed: {exc}"])

    async def _finish_plan_audit(self, project: dict[str, Any]) -> None:
        workflow = project["workflow"]
        audit_phase = self._current_phase(workflow)
        if workspace_hashes(self.workspace) != (workflow.get("review_snapshot") or {}):
            await self._wait_with_errors(["artifact_changed: plan reviewer modified workspace"])
            return
        try:
            review = parse_review(
                self._last_assistant_text, review_type="plan", problem_id=None
            )
        except ScientificContractError as exc:
            if audit_phase and int(audit_phase.get("attempts") or 0) < 2:
                await self._start_plan_audit(project, validate_execution_plan(self.workspace))
            else:
                await self._wait_with_errors([f"review_protocol: {exc}"])
            return
        _write_json(self.workspace / "reports" / "PLAN_AUDIT.json", review)
        workflow["last_review"] = review
        if review["verdict"] == "accept":
            plan = validate_execution_plan(self.workspace)
            expand_problem_phases(workflow, plan)
            workflow.pop("review_snapshot", None)
            self._save_project(project)
            await self._complete_current(project, None)
            return
        if audit_phase and int(audit_phase.get("attempts") or 0) < 2:
            planning = next(item for item in workflow["phases"] if item["id"] == "planning")
            planning["status"] = "running"
            planning["attempts"] = int(planning.get("attempts") or 1) + 1
            workflow["current"] = "planning"
            workflow["mode"] = "plan_revision"
            workflow["stage_snapshot"] = workspace_hashes(self.workspace)
            self._save_project(project)
            try:
                await self._switch_session("planner")
                await self.system("计划审查未通过，Sol 正在执行一次完整修订", "warning")
                await self.prompt(plan_revision_prompt(review))
            except Exception as exc:
                await self._wait_with_errors([f"rpc_error: plan revision failed: {exc}"])
            return
        await self._wait_with_errors(
            [f"plan_rejected: {issue}" for issue in review["issues"]]
        )

    async def _start_scientific_review(
        self, project: dict[str, Any], problem: dict[str, Any]
    ) -> None:
        workflow = project["workflow"]
        phase = self._current_phase(workflow)
        if phase:
            phase["review_attempts"] = int(phase.get("review_attempts") or 0) + 1
            phase["review_status"] = "running"
        workflow["mode"] = "scientific_review"
        workflow["review_snapshot"] = workspace_hashes(self.workspace)
        self._save_project(project)
        try:
            await self._switch_session("planner")
            await self.system(f"Sol 正在独立科学审查 {problem['id']}")
            await self.prompt(scientific_review_prompt(problem))
        except Exception as exc:
            await self._wait_with_errors([f"rpc_error: scientific review failed: {exc}"])

    async def _start_method_replan(
        self,
        project: dict[str, Any],
        problem: dict[str, Any],
        review: dict[str, Any],
    ) -> None:
        workflow = project["workflow"]
        phase = self._current_phase(workflow)
        if not phase or int(phase.get("replan_attempts") or 0) >= 1:
            await self._wait_with_errors(
                [f"method_rejected: {issue}" for issue in review["issues"]]
            )
            return
        plan = validate_execution_plan(self.workspace)
        ids = [item["id"] for item in plan["problems"]]
        start = ids.index(problem["id"])
        phase["replan_attempts"] = 1
        phase["review_status"] = "replanning"
        workflow["mode"] = "method_replan"
        workflow["replan_base"] = plan
        workflow["stage_snapshot"] = workspace_hashes(self.workspace)
        self._save_project(project)
        try:
            await self._switch_session("planner")
            await self.system(f"Sol 正在重新规划 {problem['id']} 及未执行下游", "warning")
            await self.prompt(method_replan_prompt(problem["id"], review, ids[start:]))
        except Exception as exc:
            await self._wait_with_errors([f"rpc_error: method replan failed: {exc}"])

    async def _finish_method_replan(self, project: dict[str, Any]) -> None:
        workflow = project["workflow"]
        problem = self._problem(workflow)
        if not problem:
            await self._wait_with_errors(["method_replan: current problem missing"])
            return
        before = workflow.get("stage_snapshot") or {}
        after = workspace_hashes(self.workspace)
        changed = {
            path
            for path in set(before) | set(after)
            if before.get(path) != after.get(path)
        }
        illegal = sorted(path for path in changed if path != "execution_plan.revision.json")
        scope_errors = frozen_errors(self.workspace, workflow.get("frozen") or {})
        scope_errors.extend(
            f"artifact_changed: replanner wrote outside revision file: {path}"
            for path in illegal
        )
        if scope_errors:
            await self._wait_with_errors(scope_errors)
            return
        revision_path = self.workspace / "execution_plan.revision.json"
        try:
            revision = json.loads(revision_path.read_text(encoding="utf-8"))
            merged = merge_plan_revision(
                workflow["replan_base"], revision, problem["id"]
            )
            active_path = self.workspace / "execution_plan.json"
            previous = active_path.read_text(encoding="utf-8")
            _write_json(active_path, merged)
            try:
                normalized = validate_execution_plan(self.workspace)
                if normalized.get("schema_version") != 2:
                    raise ContractError("revised plan must remain schema v2")
            except Exception:
                active_path.write_text(previous, encoding="utf-8")
                raise
        except (OSError, json.JSONDecodeError, ContractError, ScientificContractError) as exc:
            await self._wait_with_errors([f"method_replan: {exc}"])
            return
        revision_path.unlink(missing_ok=True)
        workflow["plan_version"] = normalized["plan_version"]
        workflow.pop("replan_base", None)
        workflow["mode"] = "execute"
        phase = self._current_phase(workflow)
        if phase:
            phase["attempts"] = int(phase.get("attempts") or 1) + 1
            phase["review_status"] = "pending"
        labels = {item["id"]: item["label"] for item in normalized["problems"]}
        for item in workflow["phases"]:
            if item.get("problem_id") in labels:
                item["label"] = labels[item["problem_id"]]
        workflow["stage_snapshot"] = workspace_hashes(self.workspace)
        self._save_project(project)
        try:
            await self._switch_session("worker")
            await self.system(f"Luna 正在按修订后的计划重做 {problem['id']}")
            await self.prompt(problem_prompt(self._problem(workflow) or problem))
        except Exception as exc:
            await self._wait_with_errors([f"rpc_error: revised execution failed: {exc}"])

    async def _finish_scientific_review(self, project: dict[str, Any]) -> None:
        workflow = project["workflow"]
        problem = self._problem(workflow)
        phase = self._current_phase(workflow)
        if not problem or not phase:
            await self._wait_with_errors(["scientific_review: current problem missing"])
            return
        if workspace_hashes(self.workspace) != (workflow.get("review_snapshot") or {}):
            await self._wait_with_errors(["artifact_changed: scientific reviewer modified workspace"])
            return
        try:
            review = parse_review(
                self._last_assistant_text,
                review_type="scientific",
                problem_id=problem["id"],
            )
        except ScientificContractError as exc:
            if int(phase.get("review_attempts") or 0) < 3:
                await self._start_scientific_review(project, problem)
            else:
                await self._wait_with_errors([f"review_protocol: {exc}"])
            return
        _write_json(
            self.workspace / "reports" / f"{problem['id']}_SCIENTIFIC_REVIEW.json",
            review,
        )
        workflow["last_review"] = review
        phase["review_status"] = review["verdict"]
        workflow.pop("review_snapshot", None)
        self._save_project(project)
        if review["verdict"] == "accept":
            phase["scientific_status"] = "accepted"
            workflow["mode"] = "run"
            self._save_project(project)
            await self._complete_current(project, None)
            return
        issue_class = review["issue_class"]
        if issue_class in {"implementation", "evidence"}:
            if int(phase.get("attempts") or 1) >= 3:
                await self._wait_with_errors(
                    [f"scientific_rejected: {issue}" for issue in review["issues"]]
                )
                return
            phase["attempts"] = int(phase.get("attempts") or 1) + 1
            phase["review_status"] = "repairing"
            workflow["mode"] = "scientific_repair"
            workflow["stage_snapshot"] = workspace_hashes(self.workspace)
            self._save_project(project)
            try:
                await self._switch_session("worker")
                await self.system(
                    f"Luna 正在修复 {problem['id']} 的科学审查问题", "warning"
                )
                await self.prompt(scientific_repair_prompt(problem, review))
            except Exception as exc:
                await self._wait_with_errors([f"rpc_error: scientific repair failed: {exc}"])
            return
        await self._start_method_replan(project, problem, review)

    async def _repair_candidate_v2(self, errors: list[str]) -> None:
        project = self._project()
        workflow = project["workflow"]
        phase = self._current_phase(workflow)
        if not phase or int(phase.get("attempts") or 1) >= 3:
            await self._wait_with_errors(errors)
            return
        phase["attempts"] = int(phase.get("attempts") or 1) + 1
        phase["last_error"] = "; ".join(errors)[:2000]
        workflow["mode"] = "candidate_repair"
        self._save_project(project)
        await self.system("Candidate 确定性门禁未通过，Luna 正在修复", "warning")
        await self.prompt(repair_prompt(workflow["current"], errors))

    async def _settled_v2(self, project: dict[str, Any]) -> None:
        workflow = project["workflow"]
        mode = str(workflow.get("mode") or "run")
        stage = str(workflow.get("current") or "")
        if mode == "plan_audit":
            await self._finish_plan_audit(project)
            return
        if mode == "scientific_review":
            await self._finish_scientific_review(project)
            return
        if mode == "method_replan":
            await self._finish_method_replan(project)
            return

        errors, plan = self._gate_current(project)
        if stage == "planning":
            if errors:
                if int((self._current_phase(workflow) or {}).get("attempts") or 1) >= 2:
                    await self._wait_with_errors(errors)
                else:
                    await self._repair_current(errors)
            else:
                assert plan is not None and plan.get("schema_version") == 2
                _write_json(self.workspace / "execution_plan.json", plan)
                await self._start_plan_audit(project, plan)
            return
        if stage.startswith("problem:"):
            if errors:
                await self._repair_candidate_v2(errors)
            else:
                problem = self._problem(workflow)
                if not problem:
                    await self._wait_with_errors(["candidate_protocol: problem missing"])
                else:
                    await self._start_scientific_review(project, problem)
            return
        if stage == "paper_planning" and errors:
            phase = self._current_phase(workflow)
            if phase and int(phase.get("attempts") or 1) < 2:
                phase["attempts"] = 2
                workflow["mode"] = "paper_plan_repair"
                self._save_project(project)
                await self.prompt(paper_plan_repair_prompt(errors))
            else:
                await self._wait_with_errors(errors)
            return
        if errors:
            await self._repair_current(errors)
        else:
            await self._complete_current(project, plan)

    async def _settled(self) -> None:
        async with self._transition_lock:
            if self.status in {"cancelled", "failed", "completed", "paused"}:
                return
            project_path = self.workspace / "project.json"
            if not project_path.is_file():
                return
            project = self._project()
            workflow = project.get("workflow")
            if not isinstance(workflow, dict):
                await self._legacy_settled()
                return
            if workflow.get("contract_version") == 2:
                await self._settled_v2(project)
                return
            if workflow.get("mode") == "review":
                await self._finish_review(project)
                return
            errors, plan = self._gate_current(project)
            if errors:
                await self._repair_current(errors)
            else:
                await self._complete_current(project, plan)

    async def _legacy_settled(self) -> None:
        report = self.workspace / "reports" / "VERIFY_REPORT.md"
        text = report.read_text(encoding="utf-8", errors="replace") if report.is_file() else ""
        if _verification_passed(text) and _paper_readable(self.workspace):
            self.set_status("completed")
            await self.system("完整工作流已通过验证，论文 PDF 可以下载", "success")
            await self.terminate()
        else:
            self.set_status("waiting")
            await self.system("Pi 当前回合已结束，可在左侧继续发送指令")


TASKS: dict[str, TaskRuntime] = {}


def _load_messages(workspace: Path) -> list[dict[str, Any]]:
    path = workspace / ".pi-bridge" / "messages.json"
    if not path.is_file():
        return []
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _runtime(task_id: str) -> TaskRuntime:
    workspace = _task_workspace(task_id)
    if task_id in TASKS:
        return TASKS[task_id]
    runtime = TaskRuntime(
        task_id=task_id,
        workspace=workspace,
        messages=_load_messages(workspace),
        status="stopped",
    )
    project_path = workspace / "project.json"
    if project_path.is_file():
        try:
            project = json.loads(project_path.read_text(encoding="utf-8"))
            runtime.status = str(project.get("status") or runtime.status)
            runtime.started_at = str(project.get("started_at") or runtime.started_at)
            runtime.model = str(project.get("model") or runtime.model)
            runtime.requested_model = str(project.get("model") or "")
            runtime.thinking_level = str(project.get("thinking") or "high")
            runtime.planner_model = str(
                project.get("planner_model") or runtime.requested_model
            )
            runtime.planner_thinking = str(
                project.get("planner_thinking") or runtime.thinking_level
            )
            runtime.worker_model = str(
                project.get("worker_model") or runtime.requested_model
            )
            runtime.worker_thinking = str(
                project.get("worker_thinking") or runtime.thinking_level
            )
            workflow = project.get("workflow")
            orphaned_active = (
                runtime.status in {"starting", "running"}
                and not _pid_exists(project.get("runtime_owner_pid"))
            )
            current_phase = (
                runtime._current_phase(workflow)
                if isinstance(workflow, dict)
                else None
            )
            recoverable_bridge_failure = (
                runtime.status == "failed"
                and isinstance(current_phase, dict)
                and current_phase.get("status") == "running"
                and not current_phase.get("last_error")
            )
            if (
                isinstance(workflow, dict)
                and workflow.get("contract_version") == 2
                and (orphaned_active or recoverable_bridge_failure)
            ):
                runtime.status = "paused"
                project["status"] = "paused"
                project["paused_at"] = _now()
                project["pause_reason"] = (
                    "bridge_error_recovery"
                    if recoverable_bridge_failure
                    else "bridge_restart"
                )
                phase = current_phase
                if phase:
                    phase["status_before_pause"] = phase.get("status") or "running"
                    phase["status"] = "paused"
                _write_json(project_path, project)
        except (OSError, json.JSONDecodeError):
            pass
    TASKS[task_id] = runtime
    return runtime


@asynccontextmanager
async def lifespan(_: FastAPI):
    WORKSPACES.mkdir(parents=True, exist_ok=True)
    yield
    await asyncio.gather(
        *(
            task.pause("bridge_shutdown")
            if task.status in {"starting", "running"}
            else task.terminate()
            for task in TASKS.values()
        ),
        return_exceptions=True,
    )


app = FastAPI(title="MathModelAgent Pi Bridge", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root() -> dict[str, str]:
    return {"message": "MathModelAgent Pi bridge is running"}


@app.get("/status")
async def service_status() -> dict[str, dict[str, str]]:
    pi_path = shutil.which("pi.cmd") or shutil.which("pi")
    return {
        "bridge": {"status": "running", "message": "Pi bridge is running"},
        "pi": {
            "status": "running" if pi_path else "error",
            "message": pi_path or "Pi was not found on PATH",
        },
    }


@app.get("/models")
async def available_models() -> dict[str, Any]:
    models = await asyncio.to_thread(_available_models)
    default_model = os.environ.get("MATHMODEL_PI_MODEL", "").strip()
    if default_model and not any(item["id"] == default_model for item in models):
        default_model = ""
    return {
        "models": list(models),
        "default_model": default_model,
        "default_thinking": os.environ.get(
            "MATHMODEL_PI_THINKING", "high"
        ).strip().lower(),
        "thinking_levels": list(THINKING_LEVELS),
    }


@app.post("/projects/init")
async def initialize_project(
    ques_all: str = Form(""),
    source_folder: str = Form(""),
    relative_paths: list[str] = Form(default=[]),
    files: list[UploadFile] | None = File(default=None),
) -> dict[str, Any]:
    _, summary = await _initialize_project(
        question=ques_all,
        source_folder=source_folder.strip(),
        files=files or [],
        relative_paths=relative_paths,
    )
    return summary


@app.delete("/projects/{project_id}")
async def discard_project(project_id: str) -> dict[str, bool]:
    runtime = _runtime(project_id)
    if runtime.status != "ready":
        raise HTTPException(status_code=409, detail="Only ready projects can be removed")
    TASKS.pop(project_id, None)
    await asyncio.to_thread(shutil.rmtree, runtime.workspace)
    return {"success": True}


@app.post("/projects/{project_id}/start")
async def start_project(
    project_id: str, request: StartProjectRequest
) -> dict[str, str]:
    runtime = _runtime(project_id)
    await _start_project(runtime, request)
    return {"task_id": project_id, "status": "processing"}


@app.get("/projects/{project_id}")
async def project_details(project_id: str) -> dict[str, Any]:
    runtime = _runtime(project_id)
    project = json.loads(
        (runtime.workspace / "project.json").read_text(encoding="utf-8")
    )
    manifest = json.loads(
        (runtime.workspace / "input_manifest.json").read_text(encoding="utf-8")
    )
    return {**project, "input_manifest": manifest, "workspace": str(runtime.workspace)}


@app.post("/modeling")
async def create_task(
    ques_all: str = Form(""),
    comp_template: str = Form("CUMCM"),
    language: str = Form("Chinese"),
    paper_engine: str = Form("LaTeX"),
    format_output: str = Form("LaTeX"),
    model: str = Form(""),
    thinking: str = Form("high"),
    files: list[UploadFile] | None = File(default=None),
) -> dict[str, str]:
    """Compatibility endpoint: initialize and immediately start one project."""
    runtime, summary = await _initialize_project(
        question=ques_all,
        source_folder="",
        files=files or [],
        relative_paths=[],
    )
    await _start_project(
        runtime,
        StartProjectRequest(
            question=ques_all,
            problem_file=str(summary["problem_file"]),
            competition=comp_template,
            language=language,
            paper_engine=paper_engine or format_output,
            model=model,
            thinking=thinking,
        ),
    )
    return {"task_id": runtime.task_id, "status": "processing"}


@app.get("/messages")
async def task_messages(task_id: str) -> list[dict[str, Any]]:
    return list(_runtime(task_id).messages)


@app.websocket("/task/{task_id}")
async def task_socket(websocket: WebSocket, task_id: str) -> None:
    try:
        runtime = _runtime(task_id)
    except HTTPException:
        await websocket.close(code=1008, reason="Task not found")
        return
    await websocket.accept()
    runtime.clients.add(websocket)
    try:
        while True:
            payload = await websocket.receive_json()
            if payload.get("type") != "prompt":
                continue
            text = str(payload.get("message") or "").strip()
            if not text:
                continue
            user_message = _message("user", text)
            if client_id := payload.get("id"):
                user_message["id"] = str(client_id)
            await runtime.publish(user_message)
            await runtime.prompt(text)
    except WebSocketDisconnect:
        pass
    finally:
        runtime.clients.discard(websocket)


@app.post("/modeling/{task_id}/pause")
async def pause_task(task_id: str) -> dict[str, Any]:
    runtime = _runtime(task_id)
    await runtime.pause()
    return {"success": True, "message": "Task paused and persisted"}


@app.post("/modeling/{task_id}/resume")
async def resume_task(task_id: str) -> dict[str, Any]:
    runtime = _runtime(task_id)
    await runtime.resume()
    return {"success": True, "message": "Task resumed"}


@app.post("/modeling/{task_id}/cancel")
async def cancel_task(task_id: str) -> dict[str, Any]:
    runtime = _runtime(task_id)
    if runtime.status not in {"starting", "running", "waiting", "paused"}:
        return {"success": False, "message": "Task is not running"}
    await runtime.abort()
    return {"success": True, "message": "Stop request sent"}


@app.get("/task/{task_id}/status")
async def task_status(task_id: str, request: Request) -> dict[str, Any]:
    runtime = _runtime(task_id)
    pdf = _paper_pdf(runtime.workspace)
    paper_url = None
    if pdf:
        paper_url = str(request.base_url).rstrip("/") + f"/preview/{task_id}"
    project = runtime._project()
    workflow = project.get("workflow")
    phases = _phase_statuses(runtime.workspace, runtime.status)
    profiles = None
    if isinstance(workflow, dict) and isinstance(workflow.get("phases"), list):
        phases = [
            {
                "id": str(item.get("id") or ""),
                "label": str(item.get("label") or item.get("id") or ""),
                "status": str(item.get("status") or "pending"),
                "attempts": int(item.get("attempts") or 0),
                "review_attempts": int(item.get("review_attempts") or 0),
                "replan_attempts": int(item.get("replan_attempts") or 0),
                "review_status": str(item.get("review_status") or ""),
                "scientific_status": str(item.get("scientific_status") or ""),
                "last_error": str(item.get("last_error") or ""),
            }
            for item in workflow["phases"]
        ]
        profiles = workflow.get("profiles")
    return {
        "task_id": task_id,
        "status": runtime.status,
        "model": runtime.model,
        "thinking": runtime.thinking_level,
        "profiles": profiles,
        "started_at": runtime.started_at,
        "current_stage": workflow.get("current") if isinstance(workflow, dict) else None,
        "mode": workflow.get("mode") if isinstance(workflow, dict) else None,
        "plan_version": workflow.get("plan_version") if isinstance(workflow, dict) else None,
        "contract_version": workflow.get("contract_version") if isinstance(workflow, dict) else None,
        "paused_at": project.get("paused_at"),
        "pause_reason": project.get("pause_reason"),
        "pause_count": int(project.get("pause_count") or 0),
        "resume_count": int(project.get("resume_count") or 0),
        "can_pause": runtime.status in {"starting", "running"},
        "can_resume": runtime.status == "paused" and isinstance(workflow, dict),
        "phases": phases,
        "paper_url": paper_url,
    }


@app.get("/files")
async def list_files(task_id: str) -> list[dict[str, Any]]:
    return _visible_files(_task_workspace(task_id))


@app.get("/preview/{task_id}")
async def preview_paper(task_id: str) -> FileResponse:
    pdf = _paper_pdf(_task_workspace(task_id))
    if not pdf:
        raise HTTPException(status_code=404, detail="Paper is not available")
    return FileResponse(pdf, media_type="application/pdf")


@app.get("/static/{task_id}/{filename:path}")
async def preview_asset(task_id: str, filename: str) -> FileResponse:
    return FileResponse(_safe_file(_task_workspace(task_id), filename))


@app.get("/download")
async def download_file(task_id: str, filename: str) -> FileResponse:
    path = _safe_file(_task_workspace(task_id), filename)
    return FileResponse(path, filename=path.name)


@app.get("/download_url")
async def download_url(task_id: str, filename: str, request: Request) -> dict[str, str]:
    _safe_file(_task_workspace(task_id), filename)
    base = str(request.base_url).rstrip("/")
    return {
        "download_url": f"{base}/download?task_id={task_id}&filename={quote(filename)}"
    }


@app.get("/download_all")
async def download_all(task_id: str) -> FileResponse:
    workspace = _task_workspace(task_id)
    archive_dir = workspace / ".pi-bridge"
    archive_dir.mkdir(exist_ok=True)
    archive = archive_dir / f"{task_id}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for item in _visible_files(workspace):
            path = _safe_file(workspace, item["filename"])
            bundle.write(path, item["filename"])
    return FileResponse(archive, filename=f"MathModelAgent-{task_id}.zip")


@app.get("/download_all_url")
async def download_all_url(task_id: str, request: Request) -> dict[str, str]:
    _task_workspace(task_id)
    base = str(request.base_url).rstrip("/")
    return {"download_url": f"{base}/download_all?task_id={task_id}"}


@app.get("/open_folder")
async def open_folder(task_id: str) -> dict[str, str]:
    workspace = _task_workspace(task_id)
    if os.name == "nt":
        os.startfile(workspace)  # type: ignore[attr-defined]
    return {"message": "Workspace opened", "work_dir": str(workspace)}


@app.get("/writer_seque")
async def writer_sequence() -> list[str]:
    return []
