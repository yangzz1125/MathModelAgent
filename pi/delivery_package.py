"""Build a user-facing task delivery ZIP from accepted workspace artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any


class DeliveryPackageError(ValueError):
    pass


_PAPER_SOURCE_SUFFIXES = {".tex", ".bib", ".bst", ".cls", ".sty", ".typ", ".toml"}
_BLOCKED_NAMES = {".env", "credentials.json", "secrets.json"}
_BLOCKED_SUFFIXES = {
    ".key", ".pem", ".p12", ".pfx", ".pyc", ".pyo", ".log", ".tmp", ".bak"
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative_file(workspace: Path, relative: str) -> Path:
    pure = PurePosixPath(relative)
    if pure.is_absolute() or ".." in pure.parts:
        raise DeliveryPackageError(f"不安全的文件路径：{relative}")
    candidate = workspace / Path(*pure.parts)
    if candidate.is_symlink():
        raise DeliveryPackageError(f"文件不能是符号链接：{relative}")
    path = candidate.resolve()
    try:
        path.relative_to(workspace.resolve())
    except ValueError as exc:
        raise DeliveryPackageError(f"文件超出任务目录：{relative}") from exc
    if not path.is_file():
        raise DeliveryPackageError(f"文件不存在或不是普通文件：{relative}")
    return path


def _approved_files(workspace: Path, root_name: str) -> list[Path]:
    root = workspace / root_name
    if not root.is_dir():
        return []
    files = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.stat().st_size == 0:
            continue
        relative = path.relative_to(workspace)
        lower_name = path.name.lower()
        if path.is_symlink() or any(
            part.startswith(".") or part == "__pycache__" for part in relative.parts
        ):
            continue
        if lower_name in _BLOCKED_NAMES or path.suffix.lower() in _BLOCKED_SUFFIXES:
            continue
        resolved = path.resolve()
        try:
            resolved.relative_to(workspace.resolve())
        except ValueError as exc:
            raise DeliveryPackageError(
                f"文件超出任务目录：{relative.as_posix()}"
            ) from exc
        files.append(path)
    return files


def _accepted_pdf(workspace: Path, project: dict[str, Any]) -> tuple[Path, str]:
    if project.get("status") not in {"completed", "completed_with_warnings"}:
        raise DeliveryPackageError("任务尚未完成，不能生成正式交付包")
    workflow = project.get("workflow")
    if not isinstance(workflow, dict):
        raise DeliveryPackageError("任务缺少工作流验收记录")
    review = workflow.get("document_review")
    if not isinstance(review, dict) or review.get("verdict") != "accept":
        raise DeliveryPackageError("论文尚未通过文档验收")
    evidence = workflow.get("paper_visual_evidence") or workflow.get("paper_visual")
    if not isinstance(evidence, dict) or not isinstance(evidence.get("files"), dict):
        raise DeliveryPackageError("论文缺少 Host PDF 验收证据")
    relative = evidence.get("pdf")
    if (
        not isinstance(relative, str)
        or not relative.startswith("paper/")
        or not relative.lower().endswith(".pdf")
    ):
        raise DeliveryPackageError("Host PDF 验收路径无效")
    pdf = _relative_file(workspace, relative)
    if not pdf.stat().st_size:
        raise DeliveryPackageError("论文 PDF 为空")
    expected = evidence["files"].get(relative)
    if not isinstance(expected, str) or _sha256(pdf) != expected:
        raise DeliveryPackageError("论文 PDF 与 Host 验收版本不一致")
    return pdf, relative


def build_delivery_package(
    workspace: Path, task_id: str, archive: Path
) -> dict[str, Any]:
    """Create an atomic ZIP and return its manifest."""
    workspace = workspace.resolve()
    try:
        project = json.loads(
            (workspace / "project.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise DeliveryPackageError("任务状态文件缺失或损坏") from exc
    if not isinstance(project, dict) or project.get("project_id") != task_id:
        raise DeliveryPackageError("任务编号与状态文件不一致")

    pdf, pdf_source = _accepted_pdf(workspace, project)
    selected: list[tuple[Path, str, str]] = [
        (pdf, "paper/main.pdf", "论文成品")
    ]
    paper_sources = [
        path
        for path in _approved_files(workspace, "paper")
        if path.suffix.lower() in _PAPER_SOURCE_SUFFIXES
    ]
    code = _approved_files(workspace, "code")
    inputs = _approved_files(workspace, "input")
    results = _approved_files(workspace, "results")
    figures = _approved_files(workspace, "figures")
    if not paper_sources or not code or not inputs or not results:
        raise DeliveryPackageError("论文源码、程序代码、原始数据或结果数据不完整")

    selected.extend(
        (path, path.relative_to(workspace).as_posix(), "论文源码")
        for path in paper_sources
    )
    selected.extend(
        (path, path.relative_to(workspace).as_posix(), "程序代码") for path in code
    )
    selected.extend(
        (path, path.relative_to(workspace).as_posix(), "原始数据")
        for path in inputs
    )
    input_manifest = workspace / "input_manifest.json"
    if input_manifest.is_file():
        selected.append(
            (
                _relative_file(workspace, "input_manifest.json"),
                "input_manifest.json",
                "原始数据清单",
            )
        )
    selected.extend(
        (path, path.relative_to(workspace).as_posix(), "结果数据")
        for path in results
    )
    selected.extend(
        (path, path.relative_to(workspace).as_posix(), "论文图表")
        for path in figures
    )

    names = [name for _, name, _ in selected]
    if len(names) != len(set(names)):
        raise DeliveryPackageError("交付包内出现重复路径")
    records = [
        {
            "path": target,
            "source": source.relative_to(workspace).as_posix(),
            "category": category,
            "size": source.stat().st_size,
            "sha256": _sha256(source),
        }
        for source, target, category in selected
    ]
    readme = (
        f"# MathModelAgent 任务交付包\n\n"
        f"- 任务编号：`{task_id}`\n"
        f"- 任务状态：`{project['status']}`\n"
        f"- 正式论文：`paper/main.pdf`\n"
        f"- 验收来源：`{pdf_source}`（Host 文档审查通过）\n\n"
        "## 内容\n\n"
        "- `paper/`：最终 PDF 和论文源文件。\n"
        "- `code/`：求解、验证和绘图代码。\n"
        "- `input/` 和 `input_manifest.json`：赛题与原始输入数据。\n"
        "- `results/`：模型结果和验证数据。\n"
        "- `figures/`：论文使用的 PDF、PNG 等图表。\n"
        "- `manifest.json`：每个文件的来源、大小和 SHA-256。\n\n"
        "本交付包不包含 Word、API 密钥、模型会话、内部提示词、调试日志、缓存或虚拟环境。\n"
    ).encode("utf-8")
    records.append(
        {
            "path": "README.md",
            "source": "generated",
            "category": "使用说明",
            "size": len(readme),
            "sha256": hashlib.sha256(readme).hexdigest(),
        }
    )
    manifest = {
        "schema_version": 1,
        "task_id": task_id,
        "project_status": project["status"],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "paper_acceptance": {"verdict": "accept", "source": pdf_source},
        "files": sorted(records, key=lambda item: item["path"]),
    }
    manifest_bytes = json.dumps(
        manifest, ensure_ascii=False, indent=2
    ).encode("utf-8")

    archive.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{task_id}-", suffix=".zip", dir=archive.parent
    )
    os.close(handle)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as bundle:
            bundle.writestr("README.md", readme)
            bundle.writestr("manifest.json", manifest_bytes)
            bundle.writestr("reports/", b"")
            for source, target, _ in sorted(selected, key=lambda item: item[1]):
                bundle.write(source, target)
        with zipfile.ZipFile(temporary) as bundle:
            if bundle.testzip() is not None:
                raise DeliveryPackageError("交付包校验失败")
        os.replace(temporary, archive)
    finally:
        temporary.unlink(missing_ok=True)
    return manifest
