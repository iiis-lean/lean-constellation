from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import platform
import shutil
import time
from typing import Any


Validation = Callable[[Any], bool]
Summary = Callable[[Any], str | None]


@dataclass
class LatencyRecord:
    case_id: str
    fixture: str
    operation: str
    backend: str
    iteration: int
    duration_s: float
    status: str
    raw_ok: bool | None = None
    validated: bool = False
    provider: str | None = None
    fallback_provider: str | None = None
    toolkit_tool: str | None = None
    issue_code: str | None = None
    summary: str | None = None
    command: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)


class LatencyRecorder:
    def __init__(self, *, test_name: str, artifact_dir: Path, mirror_dir: Path | None = None) -> None:
        self.test_name = test_name
        self.artifact_dir = artifact_dir
        self.mirror_dir = mirror_dir
        self.records: list[LatencyRecord] = []
        self.started_at = datetime.now(UTC).isoformat()
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        if self.mirror_dir is not None:
            self.mirror_dir.mkdir(parents=True, exist_ok=True)

    def measure(
        self,
        *,
        case_id: str,
        fixture: str,
        operation: str,
        backend: str,
        iteration: int,
        func: Callable[[], Any],
        validate: Validation | None = None,
        summarize: Summary | None = None,
        details: dict[str, Any] | None = None,
    ) -> Any:
        start = time.perf_counter()
        value: Any = None
        error: BaseException | None = None
        try:
            value = func()
        except BaseException as exc:  # noqa: BLE001 - benchmark records external failures.
            error = exc
        duration = time.perf_counter() - start
        raw_ok = _raw_ok(value)
        validated = False
        if error is None:
            try:
                validated = validate(value) if validate is not None else bool(raw_ok)
            except BaseException:  # noqa: BLE001 - validation failure should become benchmark data.
                validated = False
        summary = str(error) if error is not None else (summarize(value) if summarize is not None else _summary(value))
        self.records.append(
            LatencyRecord(
                case_id=case_id,
                fixture=fixture,
                operation=operation,
                backend=backend,
                iteration=iteration,
                duration_s=round(duration, 6),
                status="error" if error is not None else ("validated" if validated else "unexpected"),
                raw_ok=raw_ok,
                validated=validated,
                provider=_optional_str(getattr(value, "provider", None)),
                fallback_provider=_optional_str(getattr(value, "fallback_provider", None)),
                toolkit_tool=_optional_str(getattr(value, "toolkit_tool", None)),
                issue_code=_optional_str(getattr(value, "issue_code", None)),
                summary=summary,
                command=_command(value),
                details=details or {},
            )
        )
        if error is not None:
            return None
        return value

    def export(self) -> tuple[Path, Path]:
        json_path = self.artifact_dir / f"{self.test_name}.json"
        md_path = self.artifact_dir / f"{self.test_name}.md"
        payload = {
            "schema_version": 1,
            "test_name": self.test_name,
            "started_at": self.started_at,
            "completed_at": datetime.now(UTC).isoformat(),
            "environment": {
                "python": platform.python_version(),
                "platform": platform.platform(),
                "lake": shutil.which("lake"),
                "lean": shutil.which("lean"),
                "toolkit_base_url_configured": bool(os.environ.get("LEAN_CONSTELLATION_REAL_TOOLKIT_BASE_URL")),
            },
            "records": [record.__dict__ for record in self.records],
        }
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        md_path.write_text(self._markdown(), encoding="utf-8")
        if self.mirror_dir is not None:
            shutil.copyfile(json_path, self.mirror_dir / json_path.name)
            shutil.copyfile(md_path, self.mirror_dir / md_path.name)
        return json_path, md_path

    def assert_required_validated(self, *, case_ids: set[str] | None = None) -> None:
        selected = [record for record in self.records if case_ids is None or record.case_id in case_ids]
        failures = [record for record in selected if not record.validated]
        assert not failures, _failure_summary(failures)

    def _markdown(self) -> str:
        lines = [
            f"# {self.test_name}",
            "",
            f"- started_at: `{self.started_at}`",
            f"- records: `{len(self.records)}`",
            "",
            "| case | fixture | backend | iter | seconds | raw_ok | validated | provider | fallback | issue | summary |",
            "| --- | --- | --- | ---: | ---: | --- | --- | --- | --- | --- | --- |",
        ]
        for record in self.records:
            lines.append(
                "| "
                + " | ".join(
                    [
                        _md(record.operation),
                        _md(record.fixture),
                        _md(record.backend),
                        str(record.iteration),
                        f"{record.duration_s:.3f}",
                        str(record.raw_ok),
                        str(record.validated),
                        _md(record.provider or ""),
                        _md(record.fallback_provider or ""),
                        _md(record.issue_code or ""),
                        _md((record.summary or "")[:160]),
                    ]
                )
                + " |"
            )
        return "\n".join(lines) + "\n"


def artifact_dirs(tmp_path: Path, test_name: str) -> tuple[Path, Path | None]:
    local = tmp_path / "lean_tool_latency" / test_name
    mirror_raw = os.environ.get("LEAN_CONSTELLATION_LEAN_LATENCY_ARTIFACT_DIR")
    mirror = Path(mirror_raw).expanduser() / test_name if mirror_raw else None
    return local, mirror


def latency_iterations(default: int = 2) -> int:
    value = os.environ.get("LEAN_CONSTELLATION_LEAN_LATENCY_ITERATIONS")
    if not value:
        return default
    return max(1, int(value))


def latency_timeout(default: int = 180) -> int:
    value = os.environ.get("LEAN_CONSTELLATION_LEAN_LATENCY_TIMEOUT")
    if not value:
        return default
    return max(1, int(value))


def has_error_diagnostic(value: Any) -> bool:
    diagnostics = getattr(value, "diagnostics", None) or []
    for item in diagnostics:
        severity = item.get("severity") if isinstance(item, dict) else getattr(item, "severity", None)
        if str(severity or "").lower() in {"error", "fatal"}:
            return True
    excerpt = str(getattr(value, "diagnostics_excerpt", "") or getattr(value, "raw_excerpt", "") or "")
    return '"severity":"error"' in excerpt or '"severity": "error"' in excerpt or "error:" in excerpt.lower()


def service_ok(value: Any) -> bool:
    return bool(getattr(value, "ok", False))


def service_failed(value: Any) -> bool:
    return getattr(value, "ok", None) is False


def _raw_ok(value: Any) -> bool | None:
    if value is None:
        return None
    if hasattr(value, "ok"):
        return bool(getattr(value, "ok"))
    return None


def _summary(value: Any) -> str | None:
    if value is None:
        return None
    summary = getattr(value, "summary", None)
    if isinstance(summary, str):
        return summary
    issues = getattr(value, "issues", None)
    if issues:
        return "; ".join(str(getattr(issue, "message", issue)) for issue in issues[:3])
    return None


def _command(value: Any) -> list[str]:
    command = getattr(value, "command", None)
    if isinstance(command, list):
        return [str(item) for item in command]
    return []


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _failure_summary(records: list[LatencyRecord]) -> str:
    return "\n".join(
        f"{record.case_id} iter={record.iteration} raw_ok={record.raw_ok} issue={record.issue_code} summary={record.summary}"
        for record in records
    )


def _md(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")

