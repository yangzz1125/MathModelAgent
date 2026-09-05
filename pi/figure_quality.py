"""Deterministic preflight and provenance checks for generated figures."""

from __future__ import annotations

import csv
import json
import re
import xml.etree.ElementTree as ET
from importlib import metadata
from pathlib import Path, PurePosixPath
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REFERENCE_CATALOG_PATH = (
    PROJECT_ROOT
    / "pi/skills/mathmodel-figure-quality/references/figure-reference-catalog.json"
)
REFERENCE_ID_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
PACKAGE_VERSIONS = {
    "SciencePlots": "2.2.2",
    "seaborn": "0.13.2",
    "adjustText": "1.4.0",
}
CHINESE_FONTS = ("Noto Serif SC", "Noto Serif CJK SC", "Source Han Serif SC", "SimSun")
DEFAULT_STYLE_STACK = ("science", "no-latex", "bright")
SPECIALIZED_TEMPLATE_IDS = {
    "multiclass-shap-combo",
    "paired-raincloud",
    "cv-roc-ci",
    "taylor-diagram",
    "correlation-pairgrid",
    "prediction-marginal-grid",
    "rf-tpe-surface",
    "grouped-corr-split-violin",
    "grouped-circular-heatmap",
    "urban-park-cooling-combo",
    "nature-chord-diagram",
}
FIGURE_FIELDS = {
    "path",
    "preview_path",
    "spec_id",
    "reference_id",
    "claim_ids",
    "purpose",
    "plot_family",
    "generator_path",
    "data_paths",
    "required_data_fields",
    "style_stack",
    "language",
    "checks",
}
FIGURE_SPEC_FIELDS = {
    "id",
    "claim_ids",
    "purpose",
    "plot_family",
    "reference_id",
    "panels",
    "primary_encoding",
    "secondary_encoding",
    "required_annotations",
    "final_width",
    "vector_path",
    "preview_path",
    "generator_path",
    "data_paths",
    "required_data_fields",
}
REFERENCE_FIELDS = {
    "id",
    "preview_path",
    "source_url",
    "license",
    "plot_family",
    "appropriate_for",
    "avoid_when",
    "structure",
    "specialized_template_id",
    "evidence_eligible",
}
FINAL_WIDTHS = {"single_column", "double_column", "full"}
PRIMARY_ENCODINGS = {"position", "length", "color", "area", "angle"}
REQUIRED_CHECKS = {
    "source_data_loaded",
    "vector_exported",
    "preview_rendered",
    "final_size_checked",
    "grayscale_checked",
    "labels_checked",
}


def _relative(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    raw = value.strip().replace("\\", "/")
    path = PurePosixPath(raw)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"{label} is not a safe relative path: {raw}")
    return path.as_posix()


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _text_list(value: Any, label: str, *, empty: bool = False) -> list[str]:
    if not isinstance(value, list) or (not value and not empty):
        raise ValueError(f"{label} must be {'a list' if empty else 'a non-empty list'}")
    return [_text(item, f"{label}[]") for item in value]


def figure_reference_catalog() -> dict[str, dict[str, Any]]:
    """Load and validate the small local visual-reference catalog."""
    try:
        raw = json.loads(REFERENCE_CATALOG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"reference catalog is missing or invalid: {exc}") from exc
    if not isinstance(raw, dict) or set(raw) != {"schema_version", "references"}:
        raise ValueError("reference catalog top-level keys mismatch")
    if raw["schema_version"] != 1 or not isinstance(raw["references"], list):
        raise ValueError("reference catalog schema_version/references mismatch")

    catalog: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(raw["references"]):
        label = f"references[{index}]"
        if not isinstance(item, dict) or set(item) != REFERENCE_FIELDS:
            raise ValueError(f"{label} keys mismatch")
        reference_id = _text(item["id"], f"{label}.id")
        if not REFERENCE_ID_RE.fullmatch(reference_id) or reference_id in catalog:
            raise ValueError(f"{label}.id is invalid or duplicate: {reference_id}")
        preview = _relative(item["preview_path"], f"{label}.preview_path")
        if not (PROJECT_ROOT / preview).is_file():
            raise ValueError(f"{label}.preview_path does not exist: {preview}")
        specialized = item["specialized_template_id"]
        if specialized is not None and specialized not in SPECIALIZED_TEMPLATE_IDS:
            raise ValueError(f"{label}.specialized_template_id is unknown: {specialized}")
        if item["evidence_eligible"] is not False:
            raise ValueError(f"{label}.evidence_eligible must be false")
        normalized = {
            **item,
            "id": reference_id,
            "preview_path": preview,
            "source_url": _text(item["source_url"], f"{label}.source_url"),
            "license": _text(item["license"], f"{label}.license"),
            "plot_family": _text(item["plot_family"], f"{label}.plot_family"),
            "appropriate_for": _text(item["appropriate_for"], f"{label}.appropriate_for"),
            "avoid_when": _text(item["avoid_when"], f"{label}.avoid_when"),
            "structure": _text(item["structure"], f"{label}.structure"),
        }
        catalog[reference_id] = normalized
    if len(catalog) != 31:
        raise ValueError(f"reference catalog must contain 31 entries, got {len(catalog)}")
    return catalog


def figure_reference_catalog_errors() -> list[str]:
    try:
        figure_reference_catalog()
    except ValueError as exc:
        return [f"figure_preflight: {exc}"]
    return []


def validate_figure_specs(problem: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate Planner-owned figure intent before a Worker can draw."""
    problem_id = _text(problem.get("id"), "problem.id")
    outputs = [str(path).replace("\\", "/") for path in problem.get("outputs") or []]
    raw_specs = problem.get("figure_specs")
    has_figure_output = any(path.startswith(f"figures/{problem_id}/") for path in outputs)
    if raw_specs is None:
        if has_figure_output:
            raise ValueError(f"{problem_id}.figure_specs is required for declared figures")
        return []
    if not isinstance(raw_specs, list):
        raise ValueError(f"{problem_id}.figure_specs must be a list")
    if has_figure_output and not raw_specs:
        raise ValueError(f"{problem_id}.figure_specs cannot be empty for declared figures")

    catalog = figure_reference_catalog()
    expected_claims = {str(claim.get("id")) for claim in problem.get("claims") or []}
    seen_ids: set[str] = set()
    seen_artifacts: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(raw_specs):
        label = f"{problem_id}.figure_specs[{index}]"
        if not isinstance(item, dict) or set(item) != FIGURE_SPEC_FIELDS:
            raise ValueError(f"{label} keys mismatch")
        spec_id = _text(item["id"], f"{label}.id")
        if not REFERENCE_ID_RE.fullmatch(spec_id) or spec_id in seen_ids:
            raise ValueError(f"{label}.id is invalid or duplicate: {spec_id}")
        seen_ids.add(spec_id)
        claim_ids = _text_list(item["claim_ids"], f"{label}.claim_ids")
        if len(set(claim_ids)) != len(claim_ids) or not set(claim_ids) <= expected_claims:
            raise ValueError(f"{label}.claim_ids contains duplicate or unknown claims")
        reference_id = _text(item["reference_id"], f"{label}.reference_id")
        reference = catalog.get(reference_id)
        if reference is None:
            raise ValueError(f"{label}.reference_id is unknown: {reference_id}")
        plot_family = _text(item["plot_family"], f"{label}.plot_family")
        if plot_family != reference["plot_family"]:
            raise ValueError(
                f"{label}.plot_family must match reference {reference_id}: "
                f"{reference['plot_family']}"
            )
        panels = _text_list(item["panels"], f"{label}.panels")
        if len(panels) > 3 or len(set(panels)) != len(panels):
            raise ValueError(f"{label}.panels must contain 1..3 distinct panels")
        primary = _text(item["primary_encoding"], f"{label}.primary_encoding")
        if primary not in PRIMARY_ENCODINGS:
            raise ValueError(f"{label}.primary_encoding is unsupported: {primary}")
        annotations = _text_list(
            item["required_annotations"], f"{label}.required_annotations", empty=True
        )
        final_width = _text(item["final_width"], f"{label}.final_width")
        if final_width not in FINAL_WIDTHS:
            raise ValueError(f"{label}.final_width is unsupported: {final_width}")
        vector = _relative(item["vector_path"], f"{label}.vector_path")
        preview = _relative(item["preview_path"], f"{label}.preview_path")
        generator = _relative(item["generator_path"], f"{label}.generator_path")
        data_paths = [
            _relative(path, f"{label}.data_paths[]")
            for path in _text_list(item["data_paths"], f"{label}.data_paths")
        ]
        required_data_fields = _text_list(
            item["required_data_fields"], f"{label}.required_data_fields"
        )
        if len(set(required_data_fields)) != len(required_data_fields):
            raise ValueError(f"{label}.required_data_fields contains duplicates")
        if not vector.startswith(f"figures/{problem_id}/") or Path(vector).suffix.lower() not in {".pdf", ".svg"}:
            raise ValueError(f"{label}.vector_path must be PDF/SVG under figures/{problem_id}/")
        if not preview.startswith(f"figures/{problem_id}/") or Path(preview).suffix.lower() != ".png":
            raise ValueError(f"{label}.preview_path must be PNG under figures/{problem_id}/")
        if not generator.startswith(f"code/{problem_id}/"):
            raise ValueError(f"{label}.generator_path must be under code/{problem_id}/")
        for path in (vector, preview, generator):
            if not _declared(path, outputs):
                raise ValueError(f"{label} artifact is not declared in outputs: {path}")
            if path in seen_artifacts:
                raise ValueError(f"{label} reuses an artifact path: {path}")
            seen_artifacts.add(path)
        for path in data_paths:
            if not _allowed_data(path, problem):
                raise ValueError(f"{label}.data_paths is outside input/current result boundary: {path}")
            if path.startswith(f"results/{problem_id}/") and not _declared(path, outputs):
                raise ValueError(f"{label} generated data is not declared in outputs: {path}")
        normalized.append({
            "id": spec_id,
            "claim_ids": claim_ids,
            "purpose": _text(item["purpose"], f"{label}.purpose"),
            "plot_family": plot_family,
            "reference_id": reference_id,
            "panels": panels,
            "primary_encoding": primary,
            "secondary_encoding": _text(item["secondary_encoding"], f"{label}.secondary_encoding"),
            "required_annotations": annotations,
            "final_width": final_width,
            "vector_path": vector,
            "preview_path": preview,
            "generator_path": generator,
            "data_paths": data_paths,
            "required_data_fields": required_data_fields,
        })
    return normalized


def available_chinese_font() -> str | None:
    from matplotlib import font_manager

    names = {font.name for font in font_manager.fontManager.ttflist}
    return next((name for name in CHINESE_FONTS if name in names), None)


def figure_stack_errors(language: str) -> list[str]:
    errors = figure_reference_catalog_errors()
    for package, expected in PACKAGE_VERSIONS.items():
        try:
            actual = metadata.version(package)
        except metadata.PackageNotFoundError:
            errors.append(f"figure_preflight: missing {package}=={expected}")
            continue
        if actual != expected:
            errors.append(
                f"figure_preflight: {package} version {actual} != required {expected}"
            )
    if language.strip().lower() in {"chinese", "zh", "zh-cn", "中文"}:
        try:
            font = available_chinese_font()
        except Exception as exc:
            errors.append(f"figure_preflight: cannot inspect Chinese fonts: {exc}")
        else:
            if not font:
                errors.append(
                    "figure_preflight: missing Chinese font " + " / ".join(CHINESE_FONTS)
                )
    return errors


def _declared(path: str, outputs: list[str]) -> bool:
    return any(path == output or path.startswith(output.rstrip("/") + "/") for output in outputs)


def _allowed_data(path: str, problem: dict[str, Any]) -> bool:
    problem_id = str(problem["id"])
    if path.startswith(f"results/{problem_id}/"):
        return True
    for declared in problem.get("inputs") or []:
        declared = str(declared).replace("\\", "/")
        if path == declared or path.startswith(declared.rstrip("/") + "/"):
            return True
    return False


def _structured_data_fields(workspace: Path, paths: list[str]) -> set[str]:
    fields: set[str] = set()
    for path in paths:
        artifact = workspace / path
        if not artifact.is_file():
            continue
        try:
            if artifact.suffix.lower() == ".csv":
                with artifact.open(newline="", encoding="utf-8-sig") as source:
                    fields.update(csv.DictReader(source).fieldnames or ())
            elif artifact.suffix.lower() == ".json":
                value = json.loads(artifact.read_text(encoding="utf-8"))
                pending = [value]
                while pending:
                    current = pending.pop()
                    if isinstance(current, dict):
                        fields.update(str(key) for key in current)
                        pending.extend(current.values())
                    elif isinstance(current, list):
                        pending.extend(current)
        except (OSError, UnicodeError, json.JSONDecodeError, csv.Error):
            continue
    return fields


def _artifact_error(workspace: Path, path: str, kind: str) -> str | None:
    artifact = workspace / path
    if not artifact.is_file() or not artifact.stat().st_size:
        return f"figure_protocol: missing or empty {kind}: {path}"
    try:
        if kind == "vector" and artifact.suffix.lower() == ".svg":
            ET.parse(artifact)
        elif kind == "vector" and not artifact.read_bytes().startswith(b"%PDF-"):
            return f"figure_protocol: invalid PDF: {path}"
        elif kind == "preview":
            from PIL import Image

            with Image.open(artifact) as image:
                image.verify()
            with Image.open(artifact) as image:
                width, height = image.size
                if min(width, height) < 300 or max(width, height) < 800:
                    return f"figure_protocol: preview too small: {path} ({width}x{height})"
                extrema = image.convert("RGB").getextrema()
                if all(low == high for low, high in extrema):
                    return f"figure_protocol: preview is blank: {path}"
    except (OSError, ET.ParseError, ValueError) as exc:
        return f"figure_protocol: unreadable {kind} {path}: {exc}"
    return None


def figure_evidence_errors(
    workspace: Path,
    problem: dict[str, Any],
    verification: dict[str, Any],
) -> list[str]:
    """Validate figure provenance without judging scientific meaning or aesthetics."""
    problem_id = str(problem["id"])
    figures = verification.get("figures")
    if not isinstance(figures, list):
        return ["figure_protocol: verification.figures must be a list"]

    errors: list[str] = []
    try:
        specs = {item["id"]: item for item in validate_figure_specs(problem)}
        references = figure_reference_catalog()
    except ValueError as exc:
        return [f"figure_protocol: invalid plan figure_specs: {exc}"]
    seen_specs: set[str] = set()
    expected_claims = {claim["id"] for claim in problem.get("claims") or []}
    outputs = [str(path).replace("\\", "/") for path in problem.get("outputs") or []]
    figure_dir = workspace / "figures" / problem_id
    generated = {
        path.relative_to(workspace).as_posix()
        for path in figure_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in {".pdf", ".svg", ".png"}
    } if figure_dir.is_dir() else set()
    represented: set[str] = set()

    if not figures and (generated or any(path.startswith(f"figures/{problem_id}/") for path in outputs)):
        errors.append("figure_protocol: declared or generated figures lack provenance")
        return errors

    for index, raw in enumerate(figures):
        label = f"verification.figures[{index}]"
        if not isinstance(raw, dict) or set(raw) != FIGURE_FIELDS:
            errors.append(f"figure_protocol: {label} keys mismatch")
            continue
        try:
            vector = _relative(raw["path"], f"{label}.path")
            preview = _relative(raw["preview_path"], f"{label}.preview_path")
            spec_id = _text(raw["spec_id"], f"{label}.spec_id")
            reference_id = _text(raw["reference_id"], f"{label}.reference_id")
            generator = _relative(raw["generator_path"], f"{label}.generator_path")
            data_paths = [
                _relative(path, f"{label}.data_paths[]")
                for path in _text_list(raw["data_paths"], f"{label}.data_paths")
            ]
            required_data_fields = _text_list(
                raw["required_data_fields"], f"{label}.required_data_fields"
            )
            claim_ids = _text_list(raw["claim_ids"], f"{label}.claim_ids")
            purpose = _text(raw["purpose"], f"{label}.purpose")
            plot_family = _text(raw["plot_family"], f"{label}.plot_family")
            style_stack = _text_list(raw["style_stack"], f"{label}.style_stack")
            language = _text(raw["language"], f"{label}.language")
            checks = set(_text_list(raw["checks"], f"{label}.checks"))
        except ValueError as exc:
            errors.append(f"figure_protocol: {exc}")
            continue

        _ = purpose, plot_family
        if spec_id in seen_specs:
            errors.append(f"figure_protocol: duplicate spec_id in {label}: {spec_id}")
        seen_specs.add(spec_id)
        spec = specs.get(spec_id)
        if spec is None:
            errors.append(f"figure_protocol: unknown figure spec: {spec_id}")
        else:
            expected_values = {
                "reference_id": reference_id,
                "claim_ids": claim_ids,
                "purpose": purpose,
                "plot_family": plot_family,
                "vector_path": vector,
                "preview_path": preview,
                "generator_path": generator,
                "data_paths": data_paths,
                "required_data_fields": required_data_fields,
            }
            for field, actual in expected_values.items():
                planned_field = "id" if field == "spec_id" else field
                if spec.get(planned_field) != actual:
                    errors.append(f"figure_protocol: {label}.{field} differs from plan spec {spec_id}")
        reference = references.get(reference_id)
        if reference is None:
            errors.append(f"figure_protocol: unknown reference_id: {reference_id}")

        if vector in represented or preview in represented:
            errors.append(f"figure_protocol: duplicate figure artifact in {label}")
        represented.update((vector, preview))
        stem = PurePosixPath(vector).with_suffix("").as_posix()
        represented.update(
            path
            for path in generated
            if PurePosixPath(path).with_suffix("").as_posix() == stem
        )
        if not vector.startswith(f"figures/{problem_id}/") or Path(vector).suffix.lower() not in {".pdf", ".svg"}:
            errors.append(f"figure_protocol: vector path outside current problem or not PDF/SVG: {vector}")
        if not preview.startswith(f"figures/{problem_id}/") or Path(preview).suffix.lower() != ".png":
            errors.append(f"figure_protocol: preview path outside current problem or not PNG: {preview}")
        if not generator.startswith(f"code/{problem_id}/") or not (workspace / generator).is_file():
            errors.append(f"figure_protocol: generator missing or outside current problem: {generator}")
        elif not _declared(generator, outputs):
            errors.append(f"figure_protocol: generator output is not declared: {generator}")
        if not _declared(vector, outputs) or not _declared(preview, outputs):
            errors.append(f"figure_protocol: figure output is not declared for {problem_id}")
        unknown_claims = sorted(set(claim_ids) - expected_claims)
        if unknown_claims:
            errors.append(f"figure_protocol: unknown figure claims: {unknown_claims}")
        if len(set(claim_ids)) != len(claim_ids):
            errors.append(f"figure_protocol: duplicate claim ids in {label}")
        for path in data_paths:
            lowered = path.lower()
            if any(token in lowered for token in ("skills/", "examples/", "previews/", "_replica")):
                errors.append(f"figure_protocol: template/example data is forbidden: {path}")
            elif not _allowed_data(path, problem):
                errors.append(f"figure_protocol: undeclared or cross-problem figure data: {path}")
            elif not (workspace / path).is_file():
                errors.append(f"figure_protocol: figure data missing: {path}")
        available_fields = _structured_data_fields(workspace, data_paths)
        missing_fields = sorted(set(required_data_fields) - available_fields)
        if missing_fields:
            errors.append(
                f"figure_protocol: required data fields absent from structured sources: {missing_fields}"
            )
        specialized = [
            style.split(":", 1)[1]
            for style in style_stack
            if style.startswith("specialized:")
        ]
        expected_specialized = reference.get("specialized_template_id") if reference else None
        if expected_specialized:
            if specialized != [expected_specialized]:
                errors.append(
                    f"figure_protocol: reference {reference_id} requires "
                    f"specialized:{expected_specialized}"
                )
        elif specialized:
            errors.append(f"figure_protocol: ordinary reference {reference_id} cannot use a specialized template")
        if specialized:
            unknown_templates = sorted(set(specialized) - SPECIALIZED_TEMPLATE_IDS)
            if unknown_templates:
                errors.append(f"figure_protocol: unknown specialized templates: {unknown_templates}")
        elif not set(DEFAULT_STYLE_STACK).issubset(style_stack):
            errors.append(
                "figure_protocol: standard figures require science/no-latex/bright styles"
            )
        if language.strip().lower() not in {"chinese", "english", "zh", "en"}:
            errors.append(f"figure_protocol: unsupported figure language: {language}")
        missing_checks = sorted(REQUIRED_CHECKS - checks)
        if missing_checks:
            errors.append(f"figure_protocol: missing figure checks: {missing_checks}")
        for path, kind in ((vector, "vector"), (preview, "preview")):
            error = _artifact_error(workspace, path, kind)
            if error:
                errors.append(error)

    missing_specs = sorted(set(specs) - seen_specs)
    if missing_specs:
        errors.append(f"figure_protocol: planned figure specs not produced: {missing_specs}")
    unrepresented = sorted(generated - represented)
    if unrepresented:
        errors.append(f"figure_protocol: generated plots absent from provenance: {unrepresented}")
    return errors
