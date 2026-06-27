from __future__ import annotations

import pytest

from lean_constellation.services.foundation import IssueSeverity, ResultErrorComponent


def test_ok_rejects_error_warnings() -> None:
    result = ResultErrorComponent()
    warning = result.issue("heads_up", "warning", severity=IssueSeverity.WARNING)

    ok = result.ok(value={"x": 1}, warnings=[warning])

    assert ok.ok is True
    assert ok.value == {"x": 1}
    assert ok.issues == [warning]

    error = result.issue("bad", "error")
    with pytest.raises(ValueError):
        result.ok(warnings=[error])


def test_fail_requires_error_issue() -> None:
    result = ResultErrorComponent()
    warning = result.issue("heads_up", "warning", severity=IssueSeverity.WARNING)
    error = result.issue("bad", "error")

    failed = result.fail(error)

    assert failed.ok is False
    assert [issue.kind for issue in failed.issues] == ["bad"]

    with pytest.raises(ValueError):
        result.fail([])
    with pytest.raises(ValueError):
        result.fail(warning)


def test_gate_failed_requires_error_issue() -> None:
    result = ResultErrorComponent()
    warning = result.issue("minor", "warning", severity=IssueSeverity.WARNING)
    error = result.issue("bad", "error")

    report = result.gate_failed("source_gate", error)

    assert report.passed is False
    assert [issue.kind for issue in report.issues] == ["bad"]

    with pytest.raises(ValueError):
        result.gate_failed("source_gate", warning)


def test_merge_gate_reports_combines_warnings_and_failures() -> None:
    result = ResultErrorComponent()
    warning = result.issue("minor", "warning", severity=IssueSeverity.WARNING)
    error = result.issue("bad", "error")

    merged = result.merge_gate_reports(
        "combined",
        [
            result.gate_passed("a", warnings=[warning]),
            result.gate_failed("b", error),
        ],
    )

    assert merged.passed is False
    assert merged.summary == "1 checks failed, 1 warnings"
    assert [issue.kind for issue in merged.issues] == ["minor", "bad"]


def test_gate_report_view_is_agent_readable() -> None:
    result = ResultErrorComponent()
    report = result.gate_passed("ready", summary="ready gate passed")

    view = result.gate_report_view(report)

    assert view.ok is True
    assert view.summary == "ready gate passed"
    assert view.value == {"gate_name": "ready", "passed": True}


def test_mutation_view_records_changed_items_and_rejects_error_warnings() -> None:
    result = ResultErrorComponent()
    warning = result.issue("minor", "warning", severity=IssueSeverity.WARNING)

    view = result.mutation_view(
        object_ref="node:Main",
        changed=True,
        summary="updated node contract",
        changed_items=["boundary", "objective"],
        auto_maintenance=["refreshed node index"],
        warnings=[warning],
    )

    assert view.object_ref == "node:Main"
    assert view.changed is True
    assert view.summary == "updated node contract"
    assert view.changed_items == ["boundary", "objective"]
    assert view.auto_maintenance == ["refreshed node index"]
    assert view.warnings == [warning]

    error = result.issue("bad", "error")
    with pytest.raises(ValueError):
        result.mutation_view(object_ref="node:Main", changed=False, summary="no change", warnings=[error])
