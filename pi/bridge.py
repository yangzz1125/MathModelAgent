"""Thin FastAPI bridge between the MathModelAgent Vue UI and Pi RPC mode."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
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
    document_review_markdown,
    merge_plan_revision,
    paper_plan_frozen_errors,
    paper_source_errors,
    parse_document_review,
    parse_method_review,
    parse_review,
    plan_completeness_receipt,
    validate_paper_manifest,
    validate_paper_plan,
)
from pi.staged_workflow import (
    ContractError,
    artifact_hashes,
    canonical_hash,
    expand_problem_phases,
    final_repair_prompt,
    final_stage_prompt,
    frozen_errors,
    initial_workflow,
    inventory_audit_prompt,
    inventory_path,
    inventory_prompt,
    inventory_revision_prompt,
    local_artifact_repair_prompt,
    method_audit_prompt,
    method_proposal_prompt,
    method_revision_prompt,
    method_spec_hash,
    method_version_dir,
    evidence_downgrade_prompt,
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
    spike_budget,
    spike_prompt,
    spike_repair_prompt,
    stage_scope_errors,
    validate_execution_plan,
    validate_method_card,
    validate_problem_inventory,
    validate_spike_report,
    workspace_hashes,
    writing_repair_prompt,
)
from pi.windows_host import (
    CREATE_NO_WINDOW,
    CREATE_SUSPENDED,
    WindowsHostBoundary,
)

ROOT = Path(__file__).resolve().parents[1]
WORKSPACES = ROOT / "workspaces"
UPSTREAM_SKILLS = ROOT / "skills"
PI_SKILLS = ROOT / "pi" / "skills"
ENTRY_SKILL = PI_SKILLS / "mathmodelagent-pi" / "SKILL.md"
TOOL_POLICY_EXTENSION = ROOT / "pi" / "tool_policy.ts"
FIGURE_REFERENCE_CATALOG = (
    PI_SKILLS / "mathmodel-figure-quality" / "references" / "figure-reference-catalog.json"
)
VENV_SCRIPTS = ROOT / ".venv-pi" / "Scripts"
TASK_ID_RE = re.compile(r"^[0-9a-f]{12}$")
MAX_UPLOAD_BYTES = 100 * 1024 * 1024
MAX_PROJECT_BYTES = 500 * 1024 * 1024
RPC_STREAM_LIMIT_BYTES = 64 * 1024 * 1024
MAX_VERIFY_REPAIRS = 2
MAX_SPIKE_REPAIRS = 2
MAX_LOCAL_ARTIFACT_REPAIRS = 2
MODEL_ID_RE = re.compile(r"^[A-Za-z0-9._:/-]{1,200}$")
THINKING_LEVELS = ("off", "minimal", "low", "medium", "high", "xhigh", "max")
SCAFFOLD_DIRS = ("reports", "code", "results", "figures", "paper", "planning")
PROBLEM_SUFFIXES = {".pdf", ".md", ".txt", ".docx"}


@lru_cache(maxsize=1)
def _host_transition_key() -> bytes:
    """Load a Bridge-only signing key kept outside every Pi workspace."""
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "share")
    path = base / "MathModelAgentPi" / "host-transition.key"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        key = path.read_bytes()
    except FileNotFoundError:
        key = os.urandom(32)
        try:
            with path.open("xb") as output:
                output.write(key)
        except FileExistsError:
            key = path.read_bytes()
    if len(key) != 32:
        raise RuntimeError("MathModelAgent Host transition key is invalid")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return key


def _transition_signature(task_id: str, transition: dict[str, Any]) -> str:
    payload = json.dumps(
        {key: value for key, value in transition.items() if key != "signature"},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hmac.new(
        _host_transition_key(), task_id.encode("ascii") + b"\0" + payload, hashlib.sha256
    ).hexdigest()


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
    except (OSError, ValueError, SystemError):
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


def _file_record(workspace: Path, path: Path) -> dict[str, str]:
    return {
        "path": path.relative_to(workspace).as_posix(),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _artifact_record_valid(workspace: Path, record: Any) -> bool:
    if not isinstance(record, dict) or not isinstance(record.get("artifact_sha256"), dict):
        return False
    hashes = record["artifact_sha256"]
    report = record.get("report")
    if not hashes or not isinstance(report, dict):
        return False
    report_relative = str(report.get("path") or "")
    relative = PurePosixPath(report_relative.replace("\\", "/"))
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        return False
    report_path = workspace / relative.as_posix()
    try:
        report_value = json.loads(report_path.read_text(encoding="utf-8"))
        declared = set(report_value.get("artifact_paths") or []) | {relative.as_posix()}
    except (OSError, json.JSONDecodeError, AttributeError):
        return False
    if set(hashes) != declared or report.get("sha256") != hashes.get(relative.as_posix()):
        return False
    return all(
        (workspace / path).is_file()
        and hashlib.sha256((workspace / path).read_bytes()).hexdigest() == expected
        for path, expected in hashes.items()
        if isinstance(path, str) and isinstance(expected, str)
    ) and len(hashes) == sum(
        isinstance(path, str) and isinstance(expected, str)
        for path, expected in hashes.items()
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
        contract_version=3,
    )
    (runtime.workspace / "planning").mkdir(exist_ok=True)
    _write_json(runtime.workspace / "planning" / "ledger.json", {
        "schema_version": 1,
        "inventory": {"version": 1, "status": "candidate"},
        "problems": {},
        "plan_version": 0,
    })
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
    prompt = inventory_prompt(
        problem_file=problem_file,
        version=1,
        competition=request.competition,
        language=request.language,
        notes=request.question.strip(),
        evidence_paths=runtime._stage_context_paths(project),
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
    _tool_started_at: dict[str, float] = field(default_factory=dict)
    _pending_rpc: dict[str, list[asyncio.Future[dict[str, Any]]]] = field(
        default_factory=dict
    )
    _transition_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _write_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _budget_exceeded: bool = False
    _tool_policy_token: str = field(default_factory=lambda: uuid.uuid4().hex)
    _host_boundary: WindowsHostBoundary | None = None

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
            self._save_project(project)
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
        self._tool_started_at.clear()
        if self._host_boundary and self._host_boundary.job_handle is not None:
            assigned = self._host_boundary.job_assigned
            self._host_boundary.terminate_job()
            if self.process and self.process.returncode is None:
                if not assigned:
                    self.process.kill()
                await asyncio.wait_for(self.process.wait(), timeout=10)
            self._release_host_state()
            return
        if not self.process or self.process.returncode is not None:
            self._release_host_state()
            return
        if os.name == "nt":
            killer = await asyncio.create_subprocess_exec(
                "taskkill",
                "/PID",
                str(self.process.pid),
                "/T",
                "/F",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            _, error = await killer.communicate()
            if killer.returncode != 0:
                raise RuntimeError(
                    f"Pi process-tree termination failed: {error.decode(errors='replace')}"
                )
        else:
            self.process.terminate()
        try:
            await asyncio.wait_for(self.process.wait(), timeout=5)
        except asyncio.TimeoutError:
            self.process.kill()
            await self.process.wait()
        self._release_host_state()

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
            if (
                workflow.get("contract_version") == 3
                and workflow.get("mode") == "feasibility_spike"
                and self._tool_started_at
            ):
                now = time.monotonic()
                workflow["spike_elapsed_seconds"] = round(
                    float(workflow.get("spike_elapsed_seconds") or 0)
                    + sum(max(0.0, now - started) for started in self._tool_started_at.values()),
                    3,
                )
                self._tool_started_at.clear()
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
        if stage in {"planning", "plan_audit", "paper_planning", "verify", "inventory", "inventory_audit"}:
            return "planner"
        if stage.startswith("method:") or stage.startswith("method_audit:"):
            return "planner"
        if mode in {
            "plan_audit", "plan_revision", "scientific_review", "method_replan",
            "inventory", "inventory_audit", "inventory_revision", "method_proposal",
            "method_audit", "method_revision", "evidence_downgrade",
        }:
            return "planner"
        return "worker"

    def _with_review_protocol_repair(
        self, prompt: str, phase: dict[str, Any] | None
    ) -> str:
        if not phase or int(phase.get("protocol_attempts") or 0) < 1:
            return prompt
        error = str(phase.get("last_error") or "review_protocol: invalid strict JSON")
        return (
            prompt
            + "\n\nYour previous response failed the strict JSON protocol: "
            + error.removeprefix("review_protocol: ")
            + ". Return one corrected JSON object only."
        )

    def _clear_review_protocol(self, phase: dict[str, Any] | None) -> None:
        if not phase:
            return
        phase["protocol_attempts"] = 0
        if str(phase.get("last_error") or "").startswith("review_protocol:"):
            phase["last_error"] = ""

    async def _retry_review_protocol(
        self,
        project: dict[str, Any],
        error: ScientificContractError,
        *,
        label: str,
    ) -> None:
        workflow = project["workflow"]
        phase = self._current_phase(workflow)
        attempts = int((phase or {}).get("protocol_attempts") or 0) + 1
        if phase:
            phase["protocol_attempts"] = attempts
            phase["last_error"] = f"review_protocol: {error}"
        self._save_project(project)
        if attempts >= 2:
            await self._wait_with_errors([f"review_protocol: {error}"])
            return
        await self.system(f"{label} JSON 无效，Sol 正在原只读会话重试", "warning")
        try:
            await self.prompt(self._prompt_for_current(self._project()))
        except Exception as exc:
            await self._wait_with_errors([
                f"rpc_error: {label} protocol retry failed: {exc}"
            ])

    def _resume_prompt(self, project: dict[str, Any]) -> str:
        workflow = project["workflow"]
        stage = str(workflow.get("current") or "")
        mode = str(workflow.get("mode") or "run")
        phase = self._current_phase(workflow) or {}
        errors = [str(phase.get("last_error") or "Interrupted by a user pause; recheck current artifacts before continuing.")]
        problem = self._problem(workflow)
        context_paths = self._stage_context_paths(project)
        if mode in {"inventory", "inventory_revision"}:
            version = int(workflow.get("inventory_version") or 1)
            base = (
                inventory_prompt(
                    problem_file=str(project["problem_file"]),
                    version=version,
                    competition=str(project.get("competition") or "CUMCM"),
                    language=str(project.get("language") or "Chinese"),
                    notes="",
                    evidence_paths=context_paths,
                )
                if mode == "inventory"
                else inventory_revision_prompt(
                    workflow.get("last_review") or {}, version, context_paths
                )
            )
            if int(phase.get("local_repair_attempts") or 0):
                return local_artifact_repair_prompt(
                    base,
                    artifact="Inventory",
                    version=version,
                    errors=errors,
                )
            return base
        if mode == "inventory_audit":
            return self._with_review_protocol_repair(
                inventory_audit_prompt(self._inventory(workflow), context_paths),
                phase,
            )
        if mode in {"method_proposal", "method_revision", "evidence_downgrade"}:
            problem_id = self._phase_problem_id(workflow)
            if problem_id:
                inventory = self._inventory(workflow)
                version = self._proposal_version(workflow)
                if mode == "method_proposal":
                    base = method_proposal_prompt(
                        inventory,
                        problem_id,
                        version,
                        canonical_hash(inventory),
                        context_paths,
                    )
                elif mode == "evidence_downgrade":
                    base = evidence_downgrade_prompt(
                        inventory,
                        problem_id,
                        version,
                        workflow.get("last_review") or {},
                        context_paths,
                    )
                else:
                    base = method_revision_prompt(
                        inventory,
                        problem_id,
                        version,
                        workflow.get("last_review") or {},
                        context_paths,
                    )
                if int(phase.get("local_repair_attempts") or 0):
                    return local_artifact_repair_prompt(
                        base,
                        artifact=f"Method Card for {problem_id}",
                        version=version,
                        errors=errors,
                    )
                return base
        if mode == "feasibility_spike":
            card = self._method_card(workflow)
            if card:
                if int(phase.get("local_repair_attempts") or 0):
                    return spike_repair_prompt(
                        card,
                        errors,
                        supplemental=bool(workflow.get("supplemental_spike")),
                        supplemental_ids=list(workflow.get("supplemental_spike_ids") or []),
                    )
                return spike_prompt(
                    card,
                    supplemental=bool(workflow.get("supplemental_spike")),
                    supplemental_ids=list(workflow.get("supplemental_spike_ids") or []),
                    evidence_paths=context_paths,
                )
        if mode == "method_audit":
            card = self._method_card(workflow)
            if card:
                primary_spike = self._spike_report(workflow, card, supplemental=False)
                spike: dict[str, Any] = primary_spike
                if workflow.get("supplemental_spike"):
                    spike = {
                        "primary": primary_spike,
                        "supplemental": self._spike_report(
                            workflow, card, supplemental=True
                        ),
                    }
                inventory_problem = next(
                    item for item in self._inventory(workflow)["problems"]
                    if item["id"] == card["problem_id"]
                )
                return self._with_review_protocol_repair(
                    method_audit_prompt(inventory_problem, card, spike, context_paths),
                    phase,
                )
        if mode == "plan_audit":
            return self._with_review_protocol_repair(
                plan_audit_prompt(context_paths), phase
            )
        if mode == "plan_revision":
            return plan_revision_prompt(workflow.get("last_review") or {}, context_paths)
        if mode == "scientific_review" and problem:
            return self._with_review_protocol_repair(
                scientific_review_prompt(
                    problem,
                    context_paths,
                    self._figure_reference_context(problem),
                ),
                phase,
            )
        if mode == "scientific_repair" and problem:
            return scientific_repair_prompt(
                problem, workflow.get("last_review") or {}, context_paths
            )
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
            "--extension",
            str(TOOL_POLICY_EXTENSION),
            "--session-dir",
            str(session_dir),
            "--name",
            f"MathModelAgent {self.task_id}",
        ]
        try:
            workflow = self._project().get("workflow") or {}
            if self._reviewer_capability(workflow):
                command.append("--mathmodel-review")
        except (OSError, json.JSONDecodeError):
            pass
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
                "MATHMODEL_TOOL_POLICY_TOKEN": self._tool_policy_token,
            }
        )
        contract_v3 = (self._project().get("workflow") or {}).get("contract_version") == 3
        creationflags = (
            CREATE_NO_WINDOW | (CREATE_SUSPENDED if contract_v3 else 0)
            if os.name == "nt"
            else 0
        )

        try:
            self._acquire_host_state()
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
            if self._host_boundary:
                self._host_boundary.assign_and_resume(self.process.pid)
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
                if isinstance(workflow, dict) and workflow.get("contract_version") in {2, 3}:
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
                    self._tool_started_at[tool_call_id] = time.monotonic()
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
                started = self._tool_started_at.pop(tool_call_id, None)
                if started is not None:
                    try:
                        project = self._project()
                        workflow = project.get("workflow") or {}
                        if (
                            workflow.get("contract_version") == 3
                            and workflow.get("mode") == "feasibility_spike"
                        ):
                            workflow["spike_elapsed_seconds"] = round(
                                float(workflow.get("spike_elapsed_seconds") or 0)
                                + max(0.0, time.monotonic() - started),
                                3,
                            )
                            self._save_project(project)
                    except (OSError, ValueError, TypeError, json.JSONDecodeError):
                        pass
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
        if self._host_boundary:
            self._host_boundary.save_project(project)
        else:
            _write_json(self.workspace / "project.json", project)

    def _acquire_host_state(self) -> None:
        if self._host_boundary:
            return
        workflow = self._project().get("workflow") or {}
        if workflow.get("contract_version") != 3:
            return
        boundary = WindowsHostBoundary(self.task_id, self.workspace)
        boundary.acquire()
        self._host_boundary = boundary

    def _release_host_state(self) -> None:
        if self._host_boundary:
            self._host_boundary.release()
            self._host_boundary = None

    def _ledger(self) -> dict[str, Any]:
        path = self.workspace / "planning" / "ledger.json"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ContractError(f"Host planning ledger is missing or invalid: {exc}") from exc
        if not isinstance(value, dict) or value.get("schema_version") != 1:
            raise ContractError("Host planning ledger schema is invalid")
        return value

    def _save_ledger(self, ledger: dict[str, Any]) -> None:
        if self._host_boundary:
            self._host_boundary.save_ledger(ledger)
        else:
            _write_json(self.workspace / "planning" / "ledger.json", ledger)

    def _inventory(self, workflow: dict[str, Any]) -> dict[str, Any]:
        ledger = self._ledger()
        version = int((ledger.get("inventory") or {}).get("version") or workflow.get("inventory_version") or 1)
        return validate_problem_inventory(self.workspace, version)

    def _phase_problem_id(self, workflow: dict[str, Any]) -> str | None:
        current = str(workflow.get("current") or "")
        if ":" not in current:
            return None
        kind, problem_id = current.split(":", 1)
        return problem_id if kind in {"method", "spike", "method_audit", "problem"} else None

    def _proposal_version(self, workflow: dict[str, Any]) -> int:
        if workflow.get("contract_version") != 3:
            return 1
        problem_id = self._phase_problem_id(workflow)
        if not problem_id:
            return int(workflow.get("inventory_version") or 1)
        try:
            entry = (self._ledger().get("problems") or {}).get(problem_id) or {}
            return int(entry.get("proposal_version") or workflow.get("proposal_version") or 1)
        except (ContractError, TypeError, ValueError):
            return int(workflow.get("proposal_version") or 1)

    def _method_card(self, workflow: dict[str, Any]) -> dict[str, Any] | None:
        problem_id = self._phase_problem_id(workflow)
        if not problem_id:
            return None
        try:
            return validate_method_card(
                self.workspace,
                self._inventory(workflow),
                problem_id,
                self._proposal_version(workflow),
            )
        except ContractError:
            return None

    def _spike_report(
        self,
        workflow: dict[str, Any],
        card: dict[str, Any],
        *,
        supplemental: bool | None = None,
    ) -> dict[str, Any]:
        entry = (self._ledger().get("problems") or {}).get(card["problem_id"]) or {}
        source_version = (
            card["proposal_version"]
            if supplemental
            else int(entry.get("spike_source_version") or card["proposal_version"])
        )
        return validate_spike_report(
            self.workspace,
            card,
            supplemental=(
                bool(workflow.get("supplemental_spike"))
                if supplemental is None
                else supplemental
            ),
            source_version=source_version,
            supplemental_ids=(
                set(workflow.get("supplemental_spike_ids") or [])
                if (bool(workflow.get("supplemental_spike")) if supplemental is None else supplemental)
                else None
            ),
        )

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

    def _stage_context_paths(self, project: dict[str, Any]) -> list[str]:
        """Return only the workspace files relevant to the current stage."""
        workflow = project["workflow"]
        stage = str(workflow.get("current") or "")
        mode = str(workflow.get("mode") or "run")
        paths = {str(project.get("problem_file") or ""), "input_manifest.json"}

        def add_tree(relative: str) -> None:
            root = self.workspace / relative
            if root.is_file():
                paths.add(relative)
            elif root.is_dir():
                paths.update(
                    path.relative_to(self.workspace).as_posix()
                    for path in root.rglob("*")
                    if path.is_file()
                )

        if stage in {"inventory", "inventory_audit", "planning", "plan_audit"}:
            add_tree("input")
        if stage == "inventory":
            version = int(workflow.get("inventory_version") or 1)
            if mode == "inventory_revision" and version > 1:
                paths.update({
                    f"planning/inventory/v{version - 1}/problem_inventory.json",
                    f"reports/PROBLEM_INVENTORY_v{version - 1}.md",
                })
        elif stage == "inventory_audit":
            version = int(workflow.get("inventory_version") or 1)
            paths.update({
                f"planning/inventory/v{version}/problem_inventory.json",
                f"reports/PROBLEM_INVENTORY_v{version}.md",
            })

        card = self._method_card(workflow)
        problem = self._problem(workflow) or ((card or {}).get("problem"))
        if problem is None and stage.startswith("method:"):
            problem_id = stage.split(":", 1)[1]
            problem = next(
                (
                    item
                    for item in self._inventory(workflow).get("problems", [])
                    if item.get("id") == problem_id
                ),
                None,
            )
        if isinstance(problem, dict):
            paths.update(str(path) for path in problem.get("inputs", problem.get("input_paths", [])))
            dependencies = problem.get("depends_on") or []
            frozen = workflow.get("frozen") or {}
            for dependency in dependencies:
                artifacts = frozen.get(dependency)
                if isinstance(artifacts, dict):
                    paths.update(str(path) for path in artifacts)

        problem_id = self._phase_problem_id(workflow)
        if stage.startswith("method:") and problem_id:
            version = self._proposal_version(workflow)
            if mode in {"method_revision", "evidence_downgrade"} and version > 1:
                add_tree(f"planning/methods/{problem_id}/v{version - 1}")
                paths.add(f"reports/{problem_id}_METHOD_v{version - 1}.md")
        if (
            workflow.get("contract_version") == 3
            and problem_id
            and stage.startswith(("spike:", "method_audit:", "problem:"))
        ):
            entry = (self._ledger().get("problems") or {}).get(problem_id) or {}
            for field in ("method_card", "method_report", "method_audit"):
                record = entry.get(field) or {}
                if isinstance(record, dict):
                    paths.add(str(record.get("path") or ""))
            for field in ("spike", "supplemental_spike"):
                record = entry.get(field) or {}
                if isinstance(record, dict):
                    paths.update(str(path) for path in (record.get("artifact_sha256") or {}))

        if stage.startswith("problem:"):
            paths.update({"execution_plan.json", "reports/ANALYSIS_MODELING_REPORT.md"})
            if mode in {"scientific_review", "scientific_repair", "candidate_repair", "direct_repair"} and problem_id:
                paths.update(artifact_hashes(self.workspace, problem_id))
        elif stage == "planning" and mode == "plan_revision":
            paths.update({"execution_plan.json", "reports/ANALYSIS_MODELING_REPORT.md"})
        elif stage == "plan_audit":
            paths.update({"execution_plan.json", "reports/ANALYSIS_MODELING_REPORT.md"})
        elif stage == "diagram":
            paths.update(self._paper_context_paths(project, writing=False))
            paths.update({"paper_plan.json", "reports/PAPER_PLAN.md"})
            add_tree("figures")

        return sorted(
            path for path in paths if path and (self.workspace / path).is_file()
        )

    def _figure_reference_context(
        self, problem: dict[str, Any] | None
    ) -> list[dict[str, Any]]:
        selected = {
            str(spec.get("reference_id") or "")
            for spec in (problem or {}).get("figure_specs", [])
        }
        if not selected:
            return []
        catalog = json.loads(FIGURE_REFERENCE_CATALOG.read_text(encoding="utf-8"))
        return [
            entry for entry in catalog["references"] if entry.get("id") in selected
        ]

    def _document_review_context_paths(self, project: dict[str, Any]) -> list[str]:
        paths = set(self._paper_context_paths(project, writing=True))
        paper_root = self.workspace / "paper"
        if paper_root.is_dir():
            paths.update(
                path.relative_to(self.workspace).as_posix()
                for path in paper_root.rglob("*")
                if path.is_file()
                and (
                    "rendered_pages" in path.parts
                    or path.suffix.lower()
                    in {".tex", ".typ", ".bib", ".json", ".log", ".pdf"}
                )
            )
        return sorted(paths)

    def _paper_context_paths(
        self, project: dict[str, Any], *, writing: bool
    ) -> list[str]:
        """List accepted workspace evidence without copying or rediscovering it."""
        workflow = project["workflow"]
        paths = {
            str(project.get("problem_file") or ""),
            "input_manifest.json",
            "execution_plan.json",
            "reports/ANALYSIS_MODELING_REPORT.md",
            "reports/PLAN_COMPLETENESS.json",
        }
        try:
            plan = validate_execution_plan(self.workspace)
            for problem in plan["problems"]:
                paths.add(f"reports/{problem['id']}_SCIENTIFIC_REVIEW.json")
                paths.update(
                    path
                    for path in problem["inputs"]
                    if Path(path).suffix.lower() in PROBLEM_SUFFIXES
                )
        except ContractError:
            pass
        for artifacts in (workflow.get("frozen") or {}).values():
            if isinstance(artifacts, dict):
                paths.update(str(path) for path in artifacts)
        if writing:
            paths.update({
                "paper_plan.json",
                "reports/PAPER_PLAN.md",
                "reports/DRAWIO_REPORT.md",
            })
            figure_root = self.workspace / "figures"
            if figure_root.is_dir():
                paths.update(
                    path.relative_to(self.workspace).as_posix()
                    for path in figure_root.rglob("*")
                    if path.is_file()
                )
        return sorted(
            path
            for path in paths
            if path and (self.workspace / path).is_file()
        )

    def _current_runtime_limit(self) -> int | None:
        try:
            workflow = self._project().get("workflow") or {}
            if workflow.get("contract_version") == 3 and str(workflow.get("current") or "").startswith("spike:"):
                card = self._method_card(workflow)
                if card:
                    budget = 60 if workflow.get("supplemental_spike") else spike_budget(
                        card["problem"]["runtime_limit_seconds"]
                    )
                    remaining = budget - int(float(workflow.get("spike_elapsed_seconds") or 0))
                    if remaining <= 0:
                        self._budget_exceeded = True
                    return max(1, remaining)
            problem = self._problem(workflow)
            return int(problem["runtime_limit_seconds"]) if problem else None
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None

    def _reviewer_capability(self, workflow: dict[str, Any]) -> bool:
        return str(workflow.get("current") or "") == "verify" or str(
            workflow.get("mode") or ""
        ) in {
            "review",
            "plan_audit",
            "scientific_review",
            "inventory_audit",
            "method_audit",
        }

    async def _set_tool_policy(self, *, review: bool) -> None:
        mode = "review" if review else "work"
        await self.rpc_command({
            "type": "prompt",
            "message": f"/mathmodel-tool-policy {self._tool_policy_token} {mode}",
        })

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
        await self._set_tool_policy(review=self._reviewer_capability(workflow))

    def _prompt_for_current(self, project: dict[str, Any]) -> str:
        workflow = project["workflow"]
        stage = str(workflow["current"])
        mode = str(workflow.get("mode") or "run")
        if mode in {
            "plan_audit",
            "plan_revision",
            "scientific_review",
            "scientific_repair",
        }:
            return self._resume_prompt(project)
        if workflow.get("contract_version") == 3:
            if mode in {
                "inventory", "inventory_audit", "inventory_revision", "method_proposal",
                "feasibility_spike", "method_audit", "method_revision", "evidence_downgrade",
            }:
                return self._resume_prompt(project)
        if stage == "planning":
            return planning_prompt(
                problem_file=str(project["problem_file"]),
                competition=str(project.get("competition") or "CUMCM"),
                language=str(project.get("language") or "Chinese"),
                paper_engine=str(project.get("paper_engine") or "LaTeX"),
                notes="",
            )
        if stage == "plan_audit":
            return plan_audit_prompt(self._stage_context_paths(project))
        if stage == "paper_planning":
            plan = validate_execution_plan(self.workspace)
            return paper_planning_prompt(
                int(plan.get("plan_version") or 1),
                self._paper_context_paths(project, writing=False),
            )
        problem = self._problem(workflow)
        if problem:
            return problem_prompt(
                problem,
                self._stage_context_paths(project),
                self._figure_reference_context(problem),
            )
        prompt = final_stage_prompt(
            stage,
            competition=str(project.get("competition") or "CUMCM"),
            language=str(project.get("language") or "Chinese"),
            paper_engine=str(project.get("paper_engine") or "LaTeX"),
            evidence_paths=(
                self._paper_context_paths(project, writing=True)
                if stage == "writing"
                else self._stage_context_paths(project)
                if stage == "diagram"
                else self._document_review_context_paths(project)
                if stage == "verify"
                else None
            ),
        )
        return (
            self._with_review_protocol_repair(
                prompt, self._current_phase(workflow)
            )
            if stage == "verify"
            else prompt
        )

    def _v3_mode_for_stage(self, stage: str) -> str:
        if stage == "inventory":
            return "inventory"
        if stage == "inventory_audit":
            return "inventory_audit"
        if stage.startswith("method:"):
            return "method_proposal"
        if stage.startswith("spike:"):
            return "feasibility_spike"
        if stage.startswith("method_audit:"):
            return "method_audit"
        return "execute" if stage.startswith("problem:") else "run"

    async def _begin_current(self, *, new_session: bool = True) -> None:
        project = self._project()
        workflow = project["workflow"]
        stage = str(workflow["current"])
        profile = self._profile_for_resume(project)
        if new_session:
            await self._switch_session(profile)
        project = self._project()
        workflow = project["workflow"]
        phase = self._current_phase(workflow)
        if workflow.get("contract_version") == 3 and stage.startswith("method:"):
            problem_id = stage.split(":", 1)[1]
            ledger = self._ledger()
            ledger.setdefault("problems", {}).setdefault(problem_id, {
                "proposal_version": 1,
                "status": "candidate",
                "ordinary_audits": 0,
                "supplemental_used": False,
                "superseded_versions": [],
            })
            self._save_ledger(ledger)
            workflow["proposal_version"] = int(
                ledger["problems"][problem_id]["proposal_version"]
            )
            workflow["stage_snapshot"] = workspace_hashes(self.workspace)
        if workflow.get("contract_version") == 3 and (
            stage == "inventory_audit" or stage.startswith("method_audit:")
        ):
            workflow["review_snapshot"] = workspace_hashes(self.workspace)
        if stage == "verify" and workflow.get("contract_version") in {2, 3}:
            workflow["review_snapshot"] = workspace_hashes(self.workspace)
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
                planning_version=self._proposal_version(workflow),
                supplemental_spike=bool(workflow.get("supplemental_spike")),
            )
        )
        if self._budget_exceeded:
            errors.append("performance_budget: command exceeded runtime_limit_seconds")
            self._budget_exceeded = False
        plan = None
        if stage == "inventory":
            version = int(workflow.get("inventory_version") or 1)
            report = self.workspace / "reports" / f"PROBLEM_INVENTORY_v{version}.md"
            if not report.is_file() or not report.stat().st_size:
                errors.append(f"artifact_missing: reports/PROBLEM_INVENTORY_v{version}.md")
            try:
                plan = validate_problem_inventory(self.workspace, version)
            except ContractError as exc:
                errors.append(f"validation_failed: {exc}")
        elif stage.startswith("method:"):
            problem_id = stage.split(":", 1)[1]
            version = self._proposal_version(workflow)
            report = self.workspace / "reports" / f"{problem_id}_METHOD_v{version}.md"
            if not report.is_file() or not report.stat().st_size:
                errors.append(f"artifact_missing: reports/{problem_id}_METHOD_v{version}.md")
            try:
                plan = validate_method_card(
                    self.workspace, self._inventory(workflow), problem_id, version
                )
            except ContractError as exc:
                errors.append(f"validation_failed: {exc}")
        elif stage.startswith("spike:"):
            card = self._method_card(workflow)
            if not card:
                errors.append("validation_failed: current method card is invalid")
            else:
                try:
                    plan = self._spike_report(workflow, card)
                except ContractError as exc:
                    errors.append(f"validation_failed: {exc}")
        elif stage == "planning":
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
            if workflow.get("contract_version") in {2, 3}:
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
            if workflow.get("contract_version") in {2, 3}:
                try:
                    active_plan = validate_execution_plan(self.workspace)
                    ledger = self._ledger() if workflow.get("contract_version") == 3 else None
                    errors.extend(acceptance_chain_errors(
                        self.workspace,
                        active_plan,
                        contract_version=int(workflow.get("contract_version") or 2),
                        ledger=ledger,
                    ))
                except (ContractError, OSError, json.JSONDecodeError) as exc:
                    errors.append(f"scientific_acceptance: {exc}")
            else:
                report = self.workspace / "reports" / "VERIFY_REPORT.md"
                text = report.read_text(encoding="utf-8", errors="replace") if report.is_file() else ""
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
        if workflow.get("contract_version") in {2, 3}:
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

    async def _finish_document_review(self, project: dict[str, Any]) -> None:
        workflow = project["workflow"]
        phase = self._current_phase(workflow)
        before = workflow.get("review_snapshot") or workflow.get("stage_snapshot") or {}
        if workspace_hashes(self.workspace) != before:
            await self._wait_with_errors([
                "artifact_changed: Document Reviewer modified workspace files"
            ])
            return
        try:
            review = parse_document_review(self._last_assistant_text)
        except ScientificContractError as exc:
            await self._retry_review_protocol(
                project, exc, label="Document Review"
            )
            return

        self._clear_review_protocol(phase)
        if phase:
            phase["review_status"] = review["verdict"]
        workflow["document_review"] = review
        host_errors, _ = self._gate_current(project)
        report = document_review_markdown(review, host_errors)
        (self.workspace / "reports" / "VERIFY_REPORT.md").write_text(
            report, encoding="utf-8"
        )
        workflow.pop("review_snapshot", None)
        self._save_project(project)
        if host_errors:
            await self._start_writing_repair(host_errors)
            return
        if review["verdict"] == "accept":
            await self._complete_current(project, None)
            return
        errors = [
            f"document_{review['issue_class']}: {issue}" for issue in review["issues"]
        ] + [
            f"required_repair: {repair}" for repair in review["required_repairs"]
        ]
        await self._start_writing_repair(errors)

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
        if workflow.get("contract_version") in {2, 3} and stage == "writing":
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
        if (
            workflow.get("contract_version") == 3
            and stage.startswith("problem:")
            and next_phase
            and next_phase["id"] == "paper_planning"
        ):
            active_plan = validate_execution_plan(self.workspace)
            receipt, completeness_errors = plan_completeness_receipt(
                self.workspace, active_plan, self._ledger()
            )
            if completeness_errors:
                await self._wait_with_errors(completeness_errors)
                return
            _write_json(self.workspace / "reports" / "PLAN_COMPLETENESS.json", receipt)
        workflow["mode"] = (
            self._v3_mode_for_stage(next_phase["id"])
            if workflow.get("contract_version") == 3 and next_phase
            else "run"
        )
        if workflow.get("contract_version") == 3:
            if next_phase and next_phase["id"].startswith("spike:"):
                workflow["spike_elapsed_seconds"] = 0.0
            elif stage.startswith("spike:"):
                workflow.pop("spike_elapsed_seconds", None)
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
            await self.prompt(self._prompt_for_current(self._project()))
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
            await self._retry_review_protocol(project, exc, label="Plan Audit")
            return
        self._clear_review_protocol(audit_phase)
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
                await self.prompt(self._prompt_for_current(self._project()))
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
            await self.prompt(self._prompt_for_current(self._project()))
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
            await self.prompt(self._prompt_for_current(self._project()))
        except Exception as exc:
            await self._wait_with_errors([f"rpc_error: revised execution failed: {exc}"])

    async def _finish_scientific_review(self, project: dict[str, Any]) -> None:
        workflow = project["workflow"]
        problem = self._problem(workflow)
        phase = self._current_phase(workflow)
        if not problem or not phase:
            await self._wait_with_errors(["scientific_review: current problem missing"])
            return
        review_path = self.workspace / "reports" / f"{problem['id']}_SCIENTIFIC_REVIEW.json"
        try:
            pending = (
                self._pending_transition(workflow, "scientific_review")
                if workflow.get("contract_version") == 3
                else None
            )
        except ContractError as exc:
            await self._wait_with_errors([f"scientific_review: {exc}"])
            return
        if pending:
            review = pending["review"]
        else:
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
                await self._retry_review_protocol(
                    project, exc, label="Scientific Review"
                )
                return
            self._clear_review_protocol(phase)
            if workflow.get("contract_version") == 3:
                self._authorize_transition(project, "scientific_review", review)
        _write_json(review_path, review)
        workflow["last_review"] = review
        phase["review_status"] = review["verdict"]
        if review["verdict"] == "accept":
            phase["scientific_status"] = "accepted"
            if workflow.get("contract_version") == 3:
                ledger = self._ledger()
                entry = (ledger.get("problems") or {}).get(problem["id"])
                candidate_hashes = artifact_hashes(self.workspace, problem["id"])
                if not isinstance(entry, dict):
                    await self._wait_with_errors([
                        f"scientific_acceptance: {problem['id']} method ledger is missing"
                    ])
                    return
                if entry.get("status") == "provisional":
                    entry["status"] = "accepted"
                    entry["scientific_candidate_sha256"] = candidate_hashes
                    self._save_ledger(ledger)
                elif not (
                    entry.get("status") == "accepted"
                    and entry.get("plan_problem_sha256") == canonical_hash(problem)
                    and entry.get("scientific_candidate_sha256") == candidate_hashes
                    and not result_errors(self.workspace, problem)
                ):
                    await self._wait_with_errors([
                        f"scientific_acceptance: {problem['id']} interrupted acceptance state is inconsistent"
                    ])
                    return
            workflow.pop("review_snapshot", None)
            workflow.pop("pending_transition", None)
            workflow["mode"] = "run"
            await self._complete_current(project, None)
            return
        workflow.pop("review_snapshot", None)
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
            workflow.pop("pending_transition", None)
            self._save_project(project)
            try:
                await self._switch_session("worker")
                await self.system(
                    f"Luna 正在修复 {problem['id']} 的科学审查问题", "warning"
                )
                await self.prompt(self._prompt_for_current(self._project()))
            except Exception as exc:
                await self._wait_with_errors([f"rpc_error: scientific repair failed: {exc}"])
            return
        if workflow.get("contract_version") == 3:
            await self._start_v3_method_revision_from_science(project, review)
        else:
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

    def _pending_transition(
        self, workflow: dict[str, Any], kind: str
    ) -> dict[str, Any] | None:
        pending = workflow.get("pending_transition")
        if pending is None:
            return None
        valid = (
            isinstance(pending, dict)
            and pending.get("kind") == kind
            and pending.get("stage") == workflow.get("current")
            and isinstance(pending.get("review"), dict)
            and isinstance(pending.get("ledger_before"), dict)
            and isinstance(pending.get("signature"), str)
            and hmac.compare_digest(
                pending["signature"], _transition_signature(self.task_id, pending)
            )
        )
        if not valid:
            raise ContractError("Host pending transition is invalid or forged")
        self._save_ledger(pending["ledger_before"])
        return pending

    def _authorize_transition(
        self,
        project: dict[str, Any],
        kind: str,
        review: dict[str, Any],
    ) -> None:
        workflow = project["workflow"]
        transition = {
            "kind": kind,
            "stage": workflow.get("current"),
            "review": review,
            "ledger_before": self._ledger(),
        }
        transition["signature"] = _transition_signature(self.task_id, transition)
        workflow["pending_transition"] = transition
        self._save_project(project)

    def _analysis_report_from_ledger(
        self, inventory: dict[str, Any], ledger: dict[str, Any]
    ) -> None:
        inventory_version = int((ledger.get("inventory") or {}).get("version") or 1)
        sources = [
            self.workspace / "reports" / f"PROBLEM_INVENTORY_v{inventory_version}.md"
        ]
        entries = ledger.get("problems") or {}
        for problem in inventory["problems"]:
            entry = entries.get(problem["id"])
            if isinstance(entry, dict) and entry.get("status") in {"provisional", "accepted"}:
                version = int(entry["proposal_version"])
                sources.append(
                    self.workspace / "reports" / f"{problem['id']}_METHOD_v{version}.md"
                )
        text = "# Analysis and Modeling Report\n\n" + "\n\n".join(
            source.read_text(encoding="utf-8").strip()
            for source in sources
            if source.is_file()
        )
        (self.workspace / "reports" / "ANALYSIS_MODELING_REPORT.md").write_text(
            text.rstrip() + "\n", encoding="utf-8"
        )

    def _activate_method(
        self,
        workflow: dict[str, Any],
        card: dict[str, Any],
        audit_path: Path,
    ) -> None:
        ledger = self._ledger()
        problem_id = card["problem_id"]
        entry = (ledger.get("problems") or {}).get(problem_id)
        if not isinstance(entry, dict):
            raise ContractError(f"{problem_id} method ledger entry missing")
        entry["status"] = "provisional"
        entry["plan_problem_sha256"] = canonical_hash(card["problem"])
        entry["method_audit"] = _file_record(self.workspace, audit_path)
        plan_path = self.workspace / "execution_plan.json"
        if plan_path.is_file():
            raw_plan = json.loads(plan_path.read_text(encoding="utf-8"))
            problems = list(raw_plan.get("problems") or [])
            ids = [item.get("id") for item in problems if isinstance(item, dict)]
            if problem_id in ids:
                if ids[-1] != problem_id:
                    raise ContractError("only the current last plan entry may be superseded")
                same_active_entry = canonical_hash(problems[-1]) == canonical_hash(card["problem"])
                problems[-1] = card["problem"]
            else:
                same_active_entry = False
                problems.append(card["problem"])
            plan_version = int(raw_plan.get("plan_version") or 0) + (0 if same_active_entry else 1)
        else:
            problems = [card["problem"]]
            plan_version = 1
        candidate = {
            "schema_version": 2,
            "plan_version": plan_version,
            "problems": problems,
        }
        previous = plan_path.read_text(encoding="utf-8") if plan_path.is_file() else None
        _write_json(plan_path, candidate)
        try:
            normalized = validate_execution_plan(self.workspace)
        except Exception:
            if previous is None:
                plan_path.unlink(missing_ok=True)
            else:
                plan_path.write_text(previous, encoding="utf-8")
            raise
        _write_json(plan_path, normalized)
        workflow["plan_version"] = normalized["plan_version"]
        ledger["plan_version"] = normalized["plan_version"]
        self._save_ledger(ledger)
        self._analysis_report_from_ledger(self._inventory(workflow), ledger)

    async def _restart_v3_problem_planning(
        self,
        project: dict[str, Any],
        review: dict[str, Any],
        *,
        downgrade_only: bool = False,
    ) -> None:
        workflow = project["workflow"]
        problem_id = self._phase_problem_id(workflow)
        if not problem_id:
            await self._wait_with_errors(["method_revision: current problem missing"])
            return
        ledger = self._ledger()
        problems = ledger.setdefault("problems", {})
        base_card = self._method_card(workflow)
        previous = problems.get(problem_id) or {}
        old_version = int(previous.get("proposal_version") or 0)
        if old_version:
            previous.setdefault("superseded_versions", []).append(old_version)
        previous["status"] = "superseded"
        new_version = old_version + 1
        workflow["proposal_version"] = new_version
        previous["proposal_version"] = new_version
        previous["previous_method_spec_sha256"] = previous.get("method_spec_sha256", "")
        previous.pop("method_card", None)
        previous.pop("method_audit", None)
        previous.pop("supplemental_spike", None)
        previous["supplemental_used"] = False
        self._save_ledger(ledger)
        workflow["last_review"] = review
        workflow["supplemental_spike"] = False
        workflow.pop("supplemental_spike_ids", None)
        workflow.pop("spike_elapsed_seconds", None)
        workflow["mode"] = "evidence_downgrade" if downgrade_only else "method_revision"
        workflow["current"] = f"method:{problem_id}"
        for kind in ("method", "spike", "method_audit"):
            phase = next(
                item for item in workflow["phases"] if item["id"] == f"{kind}:{problem_id}"
            )
            phase["status"] = "running" if kind == "method" else "pending"
            phase["last_error"] = "; ".join(review.get("issues") or [])[:2000]
            if kind == "spike":
                phase.pop("local_repair_attempts", None)
            if kind == "method":
                phase.pop("local_repair_attempts", None)
                phase["attempts"] = int(phase.get("attempts") or 0) + 1
                phase["started_at"] = _now()
        workflow["revision_base_evidence_levels"] = {
            claim["id"]: claim.get("evidence_level")
            for claim in (base_card or {}).get("problem", {}).get("claims", [])
        }
        if downgrade_only:
            workflow["downgrade_base_spec"] = previous.get("previous_method_spec_sha256", "")
            workflow["downgrade_base_problem"] = (base_card or {}).get("problem")
            workflow["downgrade_claim_ids"] = [
                item["claim_id"] for item in review.get("allowed_downgrades") or []
            ]
        workflow["stage_snapshot"] = workspace_hashes(self.workspace)
        workflow.pop("pending_transition", None)
        project["status"] = "running"
        self._save_project(project)
        try:
            await self._switch_session("planner")
            await self.system(
                f"Sol 正在{'校准证据等级' if downgrade_only else '定向修订方法'} {problem_id}",
                "warning",
            )
            await self.prompt(self._prompt_for_current(self._project()))
        except Exception as exc:
            await self._wait_with_errors([f"rpc_error: method revision failed: {exc}"])

    async def _finish_inventory_audit_v3(self, project: dict[str, Any]) -> None:
        workflow = project["workflow"]
        phase = self._current_phase(workflow)
        try:
            pending = self._pending_transition(workflow, "inventory_audit")
        except ContractError as exc:
            await self._wait_with_errors([f"inventory_audit: {exc}"])
            return
        if pending:
            review = pending["review"]
        else:
            if workspace_hashes(self.workspace) != (workflow.get("review_snapshot") or {}):
                await self._wait_with_errors(["artifact_changed: inventory reviewer modified workspace"])
                return
            try:
                review = parse_review(
                    self._last_assistant_text, review_type="inventory", problem_id=None
                )
            except ScientificContractError as exc:
                await self._retry_review_protocol(
                    project, exc, label="Inventory Audit"
                )
                return
            self._authorize_transition(project, "inventory_audit", review)
        version = int(workflow.get("inventory_version") or 1)
        self._clear_review_protocol(phase)
        audit_path = inventory_path(self.workspace, version).with_name("audit.json")
        _write_json(audit_path, review)
        workflow["last_review"] = review
        if review["verdict"] == "accept":
            inventory = validate_problem_inventory(self.workspace, version)
            ledger = self._ledger()
            ledger["inventory"] = {
                "version": version,
                "status": "accepted",
                **_file_record(self.workspace, inventory_path(self.workspace, version)),
                "audit": _file_record(self.workspace, audit_path),
            }
            self._save_ledger(ledger)
            expand_problem_phases(workflow, inventory)
            workflow.pop("review_snapshot", None)
            workflow.pop("pending_transition", None)
            await self._complete_current(project, None)
            return
        inventory_phase = next(item for item in workflow["phases"] if item["id"] == "inventory")
        if review["verdict"] != "blocked" and int(inventory_phase.get("attempts") or 1) < 2:
            inventory_phase.pop("local_repair_attempts", None)
            new_version = version + 1
            workflow["inventory_version"] = new_version
            workflow["current"] = "inventory"
            workflow["mode"] = "inventory_revision"
            inventory_phase.update({
                "status": "running",
                "attempts": 2,
                "started_at": _now(),
                "last_error": "; ".join(review["issues"])[:2000],
            })
            ledger = self._ledger()
            ledger["inventory"] = {"version": new_version, "status": "candidate"}
            self._save_ledger(ledger)
            workflow["stage_snapshot"] = workspace_hashes(self.workspace)
            workflow.pop("pending_transition", None)
            self._save_project(project)
            await self._switch_session("planner")
            await self.prompt(self._prompt_for_current(self._project()))
            return
        await self._wait_with_errors([f"inventory_rejected: {issue}" for issue in review["issues"]])

    async def _finish_method_artifact_v3(
        self, project: dict[str, Any], card: dict[str, Any]
    ) -> None:
        workflow = project["workflow"]
        problem_id = card["problem_id"]
        version = card["proposal_version"]
        ledger = self._ledger()
        problems = ledger.setdefault("problems", {})
        previous = problems.get(problem_id) or {}
        previous_spec = str(previous.get("previous_method_spec_sha256") or previous.get("method_spec_sha256") or "")
        if workflow.get("mode") == "method_revision":
            base_levels = workflow.get("revision_base_evidence_levels") or {}
            current_levels = {
                claim["id"]: claim.get("evidence_level")
                for claim in card["problem"].get("claims", [])
            }
            early_downgrades = sorted(
                claim_id
                for claim_id, old_level in base_levels.items()
                if old_level == "A_certified"
                and current_levels.get(claim_id) == "B_bounded_numerical"
            )
            if early_downgrades:
                await self._wait_with_errors([
                    f"evidence_downgrade: A to B requires exhausted audit authorization: {early_downgrades}"
                ])
                return
        if workflow.get("mode") == "evidence_downgrade":
            allowed = set(workflow.get("downgrade_claim_ids") or [])
            base_problem = workflow.get("downgrade_base_problem")
            if not isinstance(base_problem, dict) or {
                key: value for key, value in base_problem.items() if key != "claims"
            } != {
                key: value for key, value in card["problem"].items() if key != "claims"
            }:
                await self._wait_with_errors(["evidence_downgrade: non-claim contract fields changed"])
                return
            base_claims = {claim["id"]: claim for claim in base_problem.get("claims", [])}
            current_claims = {claim["id"]: claim for claim in card["problem"].get("claims", [])}
            if set(base_claims) != set(current_claims) or not allowed <= set(base_claims):
                await self._wait_with_errors(["evidence_downgrade: claim identity changed"])
                return
            for claim_id, base_claim in base_claims.items():
                current_claim = current_claims[claim_id]
                if claim_id not in allowed and current_claim != base_claim:
                    await self._wait_with_errors([
                        f"evidence_downgrade: unauthorized claim changed: {claim_id}"
                    ])
                    return
                if claim_id in allowed and not (
                    base_claim.get("evidence_level") == "A_certified"
                    and current_claim.get("evidence_level") == "B_bounded_numerical"
                    and current_claim.get("type") == base_claim.get("type")
                    and current_claim.get("requested_output_ids") == base_claim.get("requested_output_ids")
                ):
                    await self._wait_with_errors([
                        f"evidence_downgrade: invalid A to B change: {claim_id}"
                    ])
                    return
        card_path = method_version_dir(self.workspace, problem_id, version) / "method_card.json"
        report_path = self.workspace / "reports" / f"{problem_id}_METHOD_v{version}.md"
        entry = {
            "proposal_version": version,
            "status": "candidate",
            "method_spec_sha256": card["method_spec_sha256"],
            "method_card": _file_record(self.workspace, card_path),
            "method_report": _file_record(self.workspace, report_path),
            "ordinary_audits": int(previous.get("ordinary_audits") or 0),
            "supplemental_used": bool(previous.get("supplemental_used")),
            "supplemental_ever_used": bool(previous.get("supplemental_ever_used")),
            "superseded_versions": previous.get("superseded_versions", []),
        }
        reusable_source = previous.get("spike_source_version")
        reusable_record = previous.get("spike")
        reusable = bool(
            previous_spec
            and previous_spec == card["method_spec_sha256"]
            and reusable_source
            and _artifact_record_valid(self.workspace, reusable_record)
        )
        if reusable:
            try:
                validate_spike_report(
                    self.workspace,
                    card,
                    supplemental=False,
                    source_version=int(reusable_source),
                )
            except ContractError:
                reusable = False
        if reusable:
            entry["spike_source_version"] = int(reusable_source)
            entry["spike"] = reusable_record
        else:
            entry["spike_source_version"] = version
        problems[problem_id] = entry
        self._save_ledger(ledger)
        workflow.pop("downgrade_base_spec", None)
        workflow.pop("downgrade_base_problem", None)
        workflow.pop("downgrade_claim_ids", None)
        workflow.pop("revision_base_evidence_levels", None)
        workflow["supplemental_spike"] = False
        self._save_project(project)
        if reusable:
            method_phase = self._current_phase(workflow)
            spike_phase = next(item for item in workflow["phases"] if item["id"] == f"spike:{problem_id}")
            if method_phase:
                method_phase.update({"status": "completed", "completed_at": _now(), "last_error": ""})
            spike_phase.update({"status": "completed", "completed_at": _now(), "last_error": "", "reused_from_version": int(reusable_source)})
            audit_phase = next(item for item in workflow["phases"] if item["id"] == f"method_audit:{problem_id}")
            workflow["current"] = audit_phase["id"]
            workflow["mode"] = "method_audit"
            audit_phase.update({"status": "running", "attempts": int(audit_phase.get("attempts") or 0) + 1, "started_at": _now()})
            workflow["stage_snapshot"] = workspace_hashes(self.workspace)
            workflow["review_snapshot"] = workflow["stage_snapshot"]
            self._save_project(project)
            await self._switch_session("planner")
            await self.prompt(self._prompt_for_current(self._project()))
            return
        await self._complete_current(project, card)

    async def _retry_local_artifact_v3(
        self,
        project: dict[str, Any],
        errors: list[str],
        *,
        artifact: str,
    ) -> None:
        workflow = project["workflow"]
        phase = self._current_phase(workflow)
        repairable = errors and all(
            error.startswith(("artifact_missing:", "validation_failed:"))
            for error in errors
        )
        if not phase or not repairable:
            await self._wait_with_errors(errors)
            return
        repairs = int(phase.get("local_repair_attempts") or 0)
        if repairs >= MAX_LOCAL_ARTIFACT_REPAIRS:
            await self._wait_with_errors([
                f"{artifact}_repair_exhausted: {error}" for error in errors
            ])
            return
        phase["local_repair_attempts"] = repairs + 1
        phase["last_error"] = "; ".join(errors)[:2000]
        workflow["stage_snapshot"] = workspace_hashes(self.workspace)
        project["status"] = "running"
        self._save_project(project)
        await self.system(
            f"{artifact} 格式门禁未通过，正在执行同版本局部修复 "
            f"{repairs + 1}/{MAX_LOCAL_ARTIFACT_REPAIRS}",
            "warning",
        )
        try:
            await self.prompt(self._prompt_for_current(self._project()))
        except Exception as exc:
            await self._wait_with_errors([
                f"rpc_error: {artifact} local repair failed: {exc}"
            ])

    async def _retry_spike_v3(
        self, project: dict[str, Any], errors: list[str]
    ) -> None:
        workflow = project["workflow"]
        phase = self._current_phase(workflow)
        card = self._method_card(workflow)
        repairable = errors and all(
            error.startswith(("validation_failed:", "performance_budget:"))
            for error in errors
        )
        if not phase or not card or not repairable:
            await self._wait_with_errors(errors)
            return
        repairs = int(phase.get("local_repair_attempts") or 0)
        if repairs >= MAX_SPIKE_REPAIRS:
            await self._wait_with_errors([
                f"spike_repair_exhausted: {error}" for error in errors
            ])
            return
        phase["local_repair_attempts"] = repairs + 1
        phase["attempts"] = int(phase.get("attempts") or 1) + 1
        phase["started_at"] = _now()
        phase["last_error"] = "; ".join(errors)[:2000]
        workflow["mode"] = "feasibility_spike"
        workflow["stage_snapshot"] = workspace_hashes(self.workspace)
        project["status"] = "running"
        self._save_project(project)
        await self.system(
            f"Spike 格式或证据门禁未通过，Luna 正在执行同版本局部修复 {repairs + 1}/{MAX_SPIKE_REPAIRS}",
            "warning",
        )
        try:
            await self.prompt(spike_repair_prompt(
                card,
                errors,
                supplemental=bool(workflow.get("supplemental_spike")),
                supplemental_ids=list(workflow.get("supplemental_spike_ids") or []),
            ))
        except Exception as exc:
            await self._wait_with_errors([f"rpc_error: Spike local repair failed: {exc}"])

    async def _finish_spike_v3(
        self, project: dict[str, Any], spike: dict[str, Any]
    ) -> None:
        workflow = project["workflow"]
        card = self._method_card(workflow)
        if not card:
            await self._wait_with_errors(["spike: method card missing"])
            return
        ledger = self._ledger()
        entry = ledger["problems"][card["problem_id"]]
        source_version = (
            card["proposal_version"]
            if workflow.get("supplemental_spike")
            else int(entry.get("spike_source_version") or card["proposal_version"])
        )
        spike_dir = method_version_dir(self.workspace, card["problem_id"], source_version) / "spike"
        field = "supplemental_spike" if workflow.get("supplemental_spike") else "spike"
        if workflow.get("supplemental_spike"):
            spike_dir /= "supplemental"
            entry["supplemental_used"] = True
            entry["supplemental_ever_used"] = True
        report_path = spike_dir / "spike_report.json"
        paths = [*spike["artifact_paths"], report_path.relative_to(self.workspace).as_posix()]
        entry[field] = {
            "source_version": source_version,
            "method_spec_sha256": card["method_spec_sha256"],
            "report": _file_record(self.workspace, report_path),
            "artifact_sha256": {
                path: hashlib.sha256((self.workspace / path).read_bytes()).hexdigest()
                for path in paths
            },
        }
        self._save_ledger(ledger)
        await self._complete_current(project, spike)

    async def _finish_method_audit_v3(self, project: dict[str, Any]) -> None:
        workflow = project["workflow"]
        phase = self._current_phase(workflow)
        card = self._method_card(workflow)
        if not phase or not card:
            await self._wait_with_errors(["method_audit: current method card missing"])
            return
        try:
            pending = self._pending_transition(workflow, "method_audit")
        except ContractError as exc:
            await self._wait_with_errors([f"method_audit: {exc}"])
            return
        if pending:
            review = pending["review"]
        else:
            if workspace_hashes(self.workspace) != (workflow.get("review_snapshot") or {}):
                await self._wait_with_errors(["artifact_changed: method reviewer modified workspace"])
                return
            try:
                review = parse_method_review(
                    self._last_assistant_text, problem_id=card["problem_id"]
                )
            except ScientificContractError as exc:
                await self._retry_review_protocol(
                    project, exc, label="Method Audit"
                )
                return
            all_spike_ids = {
                item["id"]
                for group in card["spike_spec"].values()
                for item in group
            }
            if not set(review["supplemental_spike_ids"]) <= all_spike_ids:
                await self._wait_with_errors([
                    "method_audit: supplemental Spike references unknown planned IDs"
                ])
                return
            self._authorize_transition(project, "method_audit", review)
        self._clear_review_protocol(phase)
        audit_path = method_version_dir(
            self.workspace, card["problem_id"], card["proposal_version"]
        ) / f"audit_{int(phase.get('attempts') or 1)}.json"
        _write_json(audit_path, review)
        workflow["last_review"] = review
        ledger = self._ledger()
        entry = ledger["problems"][card["problem_id"]]
        entry["ordinary_audits"] = max(
            int(entry.get("ordinary_audits") or 0),
            int(phase.get("attempts") or 1),
        )
        self._save_ledger(ledger)
        if review["verdict"] == "accept":
            try:
                self._activate_method(workflow, card, audit_path)
            except (ContractError, OSError, json.JSONDecodeError) as exc:
                await self._wait_with_errors([f"method_activation: {exc}"])
                return
            workflow.pop("review_snapshot", None)
            workflow["supplemental_spike"] = False
            workflow.pop("supplemental_spike_ids", None)
            workflow.pop("pending_transition", None)
            await self._complete_current(project, None)
            return
        if review["verdict"] == "blocked":
            await self._wait_with_errors([f"method_blocked: {issue}" for issue in review["issues"]])
            return
        if review["supplemental_spike"] and not entry.get("supplemental_ever_used"):
            workflow["supplemental_spike"] = True
            workflow["supplemental_spike_ids"] = review["supplemental_spike_ids"]
            workflow["spike_elapsed_seconds"] = 0.0
            spike_phase = next(item for item in workflow["phases"] if item["id"] == f"spike:{card['problem_id']}")
            spike_phase.update({"status": "running", "attempts": int(spike_phase.get("attempts") or 1) + 1, "started_at": _now()})
            phase["status"] = "pending"
            workflow["current"] = spike_phase["id"]
            workflow["mode"] = "feasibility_spike"
            workflow["stage_snapshot"] = workspace_hashes(self.workspace)
            workflow.pop("pending_transition", None)
            self._save_project(project)
            await self._switch_session("worker")
            await self.prompt(self._prompt_for_current(self._project()))
            return
        audits = int(phase.get("attempts") or 1)
        if audits < 3:
            await self._restart_v3_problem_planning(project, review)
            return
        downgrade_ok = (
            bool(review["allowed_downgrades"])
            and not review["supplemental_spike"]
            and review["evidence_calibration"] == "fail"
            and all(
                review[name] == "pass"
                for name in (
                    "statement_alignment", "method_validity", "computational_feasibility",
                    "validation_independence", "dependency_consistency", "figure_contract",
                )
            )
            and not workflow.get("downgrade_used")
        )
        if downgrade_ok:
            workflow["downgrade_used"] = True
            await self._restart_v3_problem_planning(project, review, downgrade_only=True)
            return
        await self._wait_with_errors([f"method_rejected: {issue}" for issue in review["issues"]])

    async def _start_v3_method_revision_from_science(
        self, project: dict[str, Any], review: dict[str, Any]
    ) -> None:
        workflow = project["workflow"]
        problem_id = self._phase_problem_id(workflow)
        if not problem_id:
            await self._wait_with_errors(["method_rejected: problem missing"])
            return
        ledger = self._ledger()
        entry = (ledger.get("problems") or {}).get(problem_id)
        if isinstance(entry, dict):
            entry["status"] = "superseded"
            self._save_ledger(ledger)
        await self._restart_v3_problem_planning(project, review)

    async def _settled_v3(self, project: dict[str, Any]) -> None:
        workflow = project["workflow"]
        mode = str(workflow.get("mode") or "")
        stage = str(workflow.get("current") or "")
        if mode == "inventory_audit":
            await self._finish_inventory_audit_v3(project)
            return
        if mode == "method_audit":
            await self._finish_method_audit_v3(project)
            return
        if mode == "scientific_review":
            await self._finish_scientific_review(project)
            return
        if stage == "verify":
            await self._finish_document_review(project)
            return
        errors, artifact = self._gate_current(project)
        if stage == "inventory":
            if errors:
                await self._retry_local_artifact_v3(
                    project, errors, artifact="inventory"
                )
            else:
                assert artifact is not None
                await self._complete_current(project, artifact)
            return
        if stage.startswith("method:"):
            if errors:
                await self._retry_local_artifact_v3(
                    project, errors, artifact="method"
                )
            else:
                assert artifact is not None
                await self._finish_method_artifact_v3(project, artifact)
            return
        if stage.startswith("spike:"):
            if errors:
                await self._retry_spike_v3(project, errors)
            else:
                assert artifact is not None
                await self._finish_spike_v3(project, artifact)
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
            await self._complete_current(project, artifact)

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
        if stage == "verify":
            await self._finish_document_review(project)
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
            if workflow.get("contract_version") == 3:
                await self._settled_v3(project)
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
            WindowsHostBoundary.recover(task_id, workspace)
        except (OSError, RuntimeError, json.JSONDecodeError):
            pass
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
                and workflow.get("contract_version") in {2, 3}
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


def _freeform_prompt_allowed(runtime: "TaskRuntime") -> bool:
    try:
        workflow = runtime._project().get("workflow")
    except (OSError, json.JSONDecodeError):
        return False
    return not (
        isinstance(workflow, dict) and workflow.get("contract_version") == 3
    )


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
            if not _freeform_prompt_allowed(runtime):
                await websocket.send_json(_message(
                    "system",
                    "Contract-v3 自治任务不接受自由指令，请使用暂停、恢复或取消控制。",
                    type="warning",
                ))
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
                "local_repair_attempts": int(item.get("local_repair_attempts") or 0),
                "protocol_attempts": int(item.get("protocol_attempts") or 0),
                "review_attempts": int(item.get("review_attempts") or 0),
                "replan_attempts": int(item.get("replan_attempts") or 0),
                "review_status": str(item.get("review_status") or ""),
                "scientific_status": str(item.get("scientific_status") or ""),
                "reused_from_version": int(item.get("reused_from_version") or 0),
                "last_error": str(item.get("last_error") or ""),
            }
            for item in workflow["phases"]
        ]
        profiles = workflow.get("profiles")
        if workflow.get("contract_version") == 3:
            try:
                ledger = runtime._ledger()
                for phase in phases:
                    problem_id = str(phase["id"]).split(":", 1)[1] if ":" in str(phase["id"]) else ""
                    entry = (ledger.get("problems") or {}).get(problem_id) or {}
                    if entry:
                        phase["proposal_version"] = int(entry.get("proposal_version") or 0)
                        phase["method_status"] = str(entry.get("status") or "")
                        if str(phase["id"]).startswith("spike:"):
                            card = runtime._method_card(workflow)
                            if card and card["problem_id"] == problem_id:
                                phase["spike_budget_seconds"] = (
                                    60 if workflow.get("supplemental_spike") else spike_budget(
                                        card["problem"]["runtime_limit_seconds"]
                                    )
                                )
            except (OSError, json.JSONDecodeError, ContractError):
                pass
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
