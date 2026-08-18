from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from lean_constellation.services.foundation import FoundationContext, RepoPathClass
from lean_constellation.services.mathlib import (
    MathlibCandidateCache,
    MathlibCandidateView,
)
from tests.unit.services.repo_workspace.test_repo_release import (
    _prepare_release_repo,
)


_SOURCE_LEGACY = Path(".lean_constellation/source_draft")
_SOURCE_CURRENT = Path(".lean_constellation/work/drafts/source_corpus")
_RESOURCE_LEGACY = Path(".lean_constellation/resources/.drafts")
_RESOURCE_CURRENT = Path(".lean_constellation/work/drafts/resources")
_MATHLIB_LEGACY = Path(".lean_constellation/indexes/mathlib_candidates.json")
_MATHLIB_CURRENT = Path(".lean_constellation/work/cache/mathlib_candidates.json")
_PUBLICATION_LEGACY = Path("docs/lean-constellation/public-api")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _migration_plan(repo_root: Path) -> tuple[tuple[str, Path, Path | None], ...]:
    planned: list[tuple[str, Path, Path | None]] = []
    for source, destination in (
        (_SOURCE_LEGACY, _SOURCE_CURRENT),
        (_RESOURCE_LEGACY, _RESOURCE_CURRENT),
        (_MATHLIB_LEGACY, _MATHLIB_CURRENT),
    ):
        if (repo_root / source).exists():
            planned.append(("move", source, destination))
    if (repo_root / _PUBLICATION_LEGACY).exists():
        planned.append(("drop", _PUBLICATION_LEGACY, None))
    return tuple(planned)


def _apply_task_local_migration(
    repo_root: Path,
    plan: tuple[tuple[str, Path, Path | None], ...],
) -> None:
    for action, source_relpath, destination_relpath in plan:
        source = repo_root / source_relpath
        if action == "drop":
            shutil.rmtree(source)
            continue
        assert action == "move"
        assert destination_relpath is not None
        destination = repo_root / destination_relpath
        assert not destination.exists()
        destination.parent.mkdir(parents=True, exist_ok=True)
        source.replace(destination)


def test_task_local_migration_preserves_stable_truth_and_enables_current_loaders(
    tmp_path: Path,
) -> None:
    runtime, _ = _prepare_release_repo(tmp_path)
    context = FoundationContext(repo_root=tmp_path)

    stable_source = tmp_path / ".lean_constellation/source/article.tex"
    stable_source.parent.mkdir(parents=True, exist_ok=True)
    stable_source.write_text("\\section{Stable truth}\n", encoding="utf-8")
    stable_lean = tmp_path / "Main.lean"
    stable_lean.write_text(
        "theorem migration_stable_truth : True := by trivial\n",
        encoding="utf-8",
    )
    stable_hashes = {
        stable_source: _sha256(stable_source),
        stable_lean: _sha256(stable_lean),
    }

    legacy_page = tmp_path / _PUBLICATION_LEGACY / "legacy.md"
    legacy_page.parent.mkdir(parents=True, exist_ok=True)
    legacy_page.write_text("legacy generated publication\n", encoding="utf-8")
    legacy_page_bytes = legacy_page.read_bytes()
    rejected_legacy_publication = (
        runtime.repo_workspace.publication.prepare_publication(tmp_path)
    )
    assert not rejected_legacy_publication.ok
    assert rejected_legacy_publication.issues[0].kind == (
        "legacy_operational_path_present"
    )
    assert rejected_legacy_publication.issues[0].object_ref == (
        _PUBLICATION_LEGACY.as_posix()
    )
    assert legacy_page.read_bytes() == legacy_page_bytes

    source_current = runtime.foundation.source_corpus_draft_root(context)
    source_current.mkdir(parents=True)
    (source_current / "README.md").write_text(
        "# Draft\n\nCanonical entry: article.tex\n",
        encoding="utf-8",
    )
    (source_current / "article.tex").write_text(
        "\\section{Recoverable draft}\n",
        encoding="utf-8",
    )
    source_legacy = tmp_path / _SOURCE_LEGACY
    source_legacy.parent.mkdir(parents=True, exist_ok=True)
    source_current.replace(source_legacy)

    allocated = runtime.material.allocate_resource_draft(
        tmp_path,
        target="task-local migration fixture",
    )
    assert allocated.ok and allocated.value is not None
    draft_id = allocated.value.draft.draft_id
    resource_current = runtime.foundation.layout.resource_drafts_root(context)
    resource_legacy = tmp_path / _RESOURCE_LEGACY
    resource_legacy.parent.mkdir(parents=True, exist_ok=True)
    resource_current.replace(resource_legacy)

    candidate_id = "mc_task_local"
    candidate_cache = MathlibCandidateCache(
        candidates={
            candidate_id: MathlibCandidateView(
                candidate_id=candidate_id,
                summary="Task-local migration candidate.",
            )
        }
    )
    mathlib_current = runtime.foundation.mathlib_candidates_cache_path(context)
    assert runtime.foundation.write_json_atomic(mathlib_current, candidate_cache).ok
    mathlib_legacy = tmp_path / _MATHLIB_LEGACY
    mathlib_legacy.parent.mkdir(parents=True, exist_ok=True)
    mathlib_current.replace(mathlib_legacy)

    legacy_bytes = {
        path: path.read_bytes()
        for path in (
            source_legacy / "README.md",
            source_legacy / "article.tex",
            resource_legacy / draft_id / "draft.json",
            mathlib_legacy,
            legacy_page,
        )
    }

    for relpath in (
        _SOURCE_LEGACY,
        _RESOURCE_LEGACY,
        _MATHLIB_LEGACY,
        _PUBLICATION_LEGACY,
    ):
        assert (
            runtime.foundation.classify_repo_path(relpath).path_class
            is RepoPathClass.LEGACY_OPERATIONAL
        )

    rejected_source = runtime.material.scan_source_corpus(
        tmp_path,
        relpath=_SOURCE_LEGACY.as_posix(),
    )
    assert not rejected_source.ok
    assert rejected_source.issues[0].kind == "legacy_operational_path_forbidden"
    assert not runtime.material.get_resource_draft(tmp_path, draft_id=draft_id).ok
    assert not runtime.mathlib.inspect_mathlib_search_candidate(
        tmp_path,
        candidate_id=candidate_id,
    ).ok

    rejected_release = runtime.validation_snapshot.release_finalizer.preview_candidate_release(
        tmp_path,
        base_release_id=None,
        summary="Legacy operational paths must block.",
    )
    assert not rejected_release.ok
    assert rejected_release.issues[0].kind == "legacy_operational_path_present"
    assert {path: path.read_bytes() for path in legacy_bytes} == legacy_bytes

    plan = _migration_plan(tmp_path)
    assert plan == (
        ("move", _SOURCE_LEGACY, _SOURCE_CURRENT),
        ("move", _RESOURCE_LEGACY, _RESOURCE_CURRENT),
        ("move", _MATHLIB_LEGACY, _MATHLIB_CURRENT),
        ("drop", _PUBLICATION_LEGACY, None),
    )
    _apply_task_local_migration(tmp_path, plan)

    loaded_source = runtime.material.source_corpus.scan_source_corpus_draft(tmp_path)
    assert loaded_source.ok and loaded_source.value is not None
    assert {item.path for item in loaded_source.value.files} == {
        "README.md",
        "article.tex",
    }
    assert runtime.material.get_resource_draft(tmp_path, draft_id=draft_id).ok
    assert runtime.mathlib.inspect_mathlib_search_candidate(
        tmp_path,
        candidate_id=candidate_id,
    ).ok
    assert runtime.repo_workspace.publication.build_manifest(tmp_path).ok
    assert _migration_plan(tmp_path) == ()
    assert {path: _sha256(path) for path in stable_hashes} == stable_hashes
