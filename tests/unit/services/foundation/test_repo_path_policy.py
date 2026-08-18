from __future__ import annotations

from pathlib import PurePosixPath

import pytest

from lean_constellation.services.foundation import (
    LCWorkKind,
    RepoPathClass,
    classify_repo_path,
)


@pytest.mark.parametrize(
    ("path", "path_class", "work_kind"),
    [
        ("Main/Theorems/result.lean", RepoPathClass.PORTABLE_TRUTH, None),
        (".lean_constellation/source/article.tex", RepoPathClass.PORTABLE_TRUTH, None),
        (".lean_constellation/releases/release_1.json", RepoPathClass.PORTABLE_TRUTH, None),
        (".lean_constellation/work", RepoPathClass.OPERATIONAL_WORK, None),
        (".lean_constellation/work/future/category.json", RepoPathClass.OPERATIONAL_WORK, None),
        (
            ".lean_constellation/work/drafts/source_corpus/article.tex",
            RepoPathClass.RECOVERABLE_WORK,
            LCWorkKind.SOURCE_CORPUS_DRAFT,
        ),
        (
            ".lean_constellation/work/drafts/resources/draft_1/README.md",
            RepoPathClass.RECOVERABLE_WORK,
            LCWorkKind.RESOURCE_DRAFT,
        ),
        (
            ".lean_constellation/work/recovery/source_index/operator_baseline.json",
            RepoPathClass.RECOVERABLE_WORK,
            LCWorkKind.SOURCE_INDEX_RECOVERY,
        ),
        (
            ".lean_constellation/work/cache/mathlib_candidates.json",
            RepoPathClass.REBUILDABLE_WORK,
            LCWorkKind.MATHLIB_CANDIDATE_CACHE,
        ),
        (
            ".lean_constellation/work/previews/source_corpus/source_1/page-1.png",
            RepoPathClass.REBUILDABLE_WORK,
            LCWorkKind.SOURCE_CORPUS_PREVIEW,
        ),
        (
            ".lean_constellation/work/staging/source_corpus/transaction_1/manifest.json",
            RepoPathClass.TRANSACTION_WORK,
            LCWorkKind.SOURCE_CORPUS_STAGING,
        ),
        (
            ".lean_constellation/work/audit/gate_gaps.jsonl",
            RepoPathClass.LOCAL_EVIDENCE,
            LCWorkKind.AUDIT,
        ),
        (
            ".lean_constellation/work/receipts/remote_publication/release_1.json",
            RepoPathClass.LOCAL_EVIDENCE,
            LCWorkKind.RECEIPT,
        ),
        (".lean_constellation/snapshots/snapshot_1/manifest.json", RepoPathClass.SPECIALIZED_RECOVERY, None),
        (".lean_constellation/.locks/repo.lock", RepoPathClass.SPECIALIZED_RECOVERY, None),
        (".agent_runtime/flows/f_1.json", RepoPathClass.ARK_RUNTIME, None),
        (".runtime/server.json", RepoPathClass.PROCESS_RUNTIME, None),
        (".lake/build/lib.olean", RepoPathClass.BUILD_ARTIFACT, None),
        (".git/index", RepoPathClass.GIT_INTERNAL, None),
        ("nested/auth.json", RepoPathClass.LOCAL_SECRET, None),
        (".env.local", RepoPathClass.LOCAL_SECRET, None),
        (".env.example", RepoPathClass.PORTABLE_TRUTH, None),
    ],
)
def test_repo_path_policy_classifies_portable_operational_and_specialized_paths(
    path: str,
    path_class: RepoPathClass,
    work_kind: LCWorkKind | None,
) -> None:
    classified = classify_repo_path(PurePosixPath(path))

    assert classified.path == path
    assert classified.path_class is path_class
    assert classified.work_kind is work_kind
    assert classified.publication_eligible is (path_class is RepoPathClass.PORTABLE_TRUTH)
    assert classified.release_eligible is (path_class is RepoPathClass.PORTABLE_TRUTH)
    assert classified.semantic_digest_eligible is (path_class is RepoPathClass.PORTABLE_TRUTH)
    assert classified.requires_migration is False


@pytest.mark.parametrize(
    "path",
    [
        ".lean_constellation/source_draft/README.md",
        ".lean_constellation/resources/.drafts/draft_1/draft.json",
        ".lean_constellation/resources/tmp/request_1/result.json",
        ".lean_constellation/indexes/mathlib_candidates.json",
        ".lean_constellation/.source_corpus_staging/import_1/manifest.json",
        ".lean_constellation/source_index/operator_baseline.json",
        ".lean_constellation/audit/gate_gaps.jsonl",
        ".lean_constellation/publication/remote_receipts/release_1.json",
        ".lean_constellation/checkpoints/checkpoint_1.json",
        ".lean_constellation/locks/repo.lock",
        ".lean_constellation/staging/old.json",
        "docs/lean-constellation/public-api/old.json",
    ],
)
def test_repo_path_policy_rejects_unsafe_paths_and_marks_legacy_operational_paths(path: str) -> None:
    classified = classify_repo_path(PurePosixPath(path))

    assert classified.path_class is RepoPathClass.LEGACY_OPERATIONAL
    assert classified.requires_migration is True
    assert classified.publication_eligible is False
    assert classified.release_eligible is False
    assert classified.semantic_digest_eligible is False


@pytest.mark.parametrize(
    "path",
    [
        PurePosixPath(),
        PurePosixPath("/absolute/path"),
        PurePosixPath("safe/../escape"),
        PurePosixPath(r"ambiguous\\windows"),
    ],
)
def test_repo_path_policy_rejects_unsafe_repo_relative_paths(path: PurePosixPath) -> None:
    with pytest.raises(ValueError, match="safe repository-relative"):
        classify_repo_path(path)
