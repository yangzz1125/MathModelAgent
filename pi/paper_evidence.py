"""Host-rendered physical-page evidence bound to one PDF revision."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PIL import Image


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(command: list[str], timeout: float, cancelled: Callable[[], bool]) -> bytes:
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    started = time.monotonic()
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          creationflags=flags) as process:
        try:
            while True:
                if cancelled():
                    raise ValueError("Host rendering cancelled")
                if time.monotonic() - started >= timeout:
                    raise subprocess.TimeoutExpired(command[0], timeout)
                try:
                    stdout, stderr = process.communicate(timeout=0.1)
                    if process.returncode:
                        raise subprocess.CalledProcessError(process.returncode, command[0], stderr=stderr)
                    return stdout
                except subprocess.TimeoutExpired:
                    continue
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()


def render_paper_pages(
    workspace: Path, pdf: Path, *, cancelled: Callable[[], bool] = lambda: False
) -> dict[str, Any]:
    """Called by the Host only, after the Writing gate and before Reviewer snapshots."""
    # Windows 8.3 aliases and relative roots must use the same canonical form.
    # Retain the containment check before reading or rendering any PDF.
    workspace = workspace.resolve()
    pdf = pdf.resolve()
    pdf.relative_to(workspace)
    pdfinfo, renderer = shutil.which("pdfinfo"), shutil.which("pdftoppm")
    if not pdfinfo or not renderer:
        raise ValueError("pdfinfo and pdftoppm are required for Host page evidence")
    before = _sha256(pdf)
    info = _run([pdfinfo, str(pdf)], 30, cancelled).decode("utf-8", errors="replace")
    match = re.search(r"(?m)^Pages:\s+(\d+)\s*$", info)
    if not match or int(match[1]) < 1:
        raise ValueError("PDF has no valid physical page count")
    count = int(match[1])
    target = workspace / "paper" / "rendered_pages"
    if target.resolve() != workspace.resolve() / "paper" / "rendered_pages":
        raise ValueError("Host page directory must not redirect through a symlink/junction")
    target.mkdir(parents=True, exist_ok=True)
    files = {pdf.relative_to(workspace).as_posix(): before}
    with tempfile.TemporaryDirectory(prefix=".host-render-", dir=target.parent) as directory:
        temporary = Path(directory)
        _run([renderer, "-r", "160", "-png", str(pdf), str(temporary / "page")], 120, cancelled)
        pages = {int(path.stem.split("-")[-1]): path for path in temporary.glob("page-*.png")}
        if set(pages) != set(range(1, count + 1)):
            raise ValueError("PDF render did not produce every physical page")
        for number, source in sorted(pages.items()):
            if cancelled():
                raise ValueError("Host rendering cancelled")
            with Image.open(source) as image:
                image.load()
                gray = image.convert("L")
                if min(image.size) < 800 or len(set(gray.getextrema())) == 1:
                    raise ValueError(f"PDF page {number} is blank or below readable resolution")
                color_path = target / f"page-{number:02d}.png"
                gray_path = target / f"page-{number:02d}-gray.png"
                color_temp = temporary / "color.png"
                gray_temp = temporary / "gray.png"
                image.convert("RGB").save(color_temp)
                gray.save(gray_temp)
                # Replacing directory entries cannot follow a producer-created file link.
                os.replace(color_temp, color_path)
                os.replace(gray_temp, gray_path)
            for path in (color_path, gray_path):
                files[path.relative_to(workspace).as_posix()] = _sha256(path)
    if _sha256(pdf) != before:
        raise ValueError("PDF changed during Host rendering")
    # Remove only stale generated pages, not other Writer-owned artifacts.
    for path in target.glob("page-*.png"):
        if path.relative_to(workspace).as_posix() not in files:
            path.unlink()
    return {"pdf": pdf.relative_to(workspace).as_posix(), "page_count": count, "files": files}


def paper_visual_errors(workspace: Path, record: dict[str, Any] | None) -> list[str]:
    if not record or not isinstance(record.get("files"), dict):
        return ["artifact_changed: Host PDF/page evidence is missing"]
    count = record.get("page_count")
    if not isinstance(count, int) or isinstance(count, bool) or count < 1:
        return ["artifact_changed: Host physical page count is invalid"]
    expected = {record.get("pdf")} | {
        f"paper/rendered_pages/page-{number:02d}{suffix}.png"
        for number in range(1, count + 1) for suffix in ("", "-gray")
    }
    if set(record["files"]) != expected:
        return ["artifact_changed: Host physical page coverage is incomplete"]
    errors = []
    for relative, digest in record["files"].items():
        path = workspace / relative
        try:
            path.resolve().relative_to(workspace.resolve())
            if _sha256(path) != digest:
                errors.append(f"artifact_changed: PDF/page evidence changed: {relative}")
        except (OSError, ValueError):
            errors.append(f"artifact_changed: PDF/page evidence missing or invalid: {relative}")
    return errors
