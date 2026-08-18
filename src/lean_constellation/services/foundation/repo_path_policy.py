"""Pure classification for repository-relative Lean Constellation paths."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath


class RepoPathClass(StrEnum):
    """Stable lifecycle and ownership classes for repository paths."""

    PORTABLE_TRUTH = "portable_truth"
    OPERATIONAL_WORK = "operational_work"
    RECOVERABLE_WORK = "recoverable_work"
    REBUILDABLE_WORK = "rebuildable_work"
    TRANSACTION_WORK = "transaction_work"
    LOCAL_EVIDENCE = "local_evidence"
    SPECIALIZED_RECOVERY = "specialized_recovery"
    ARK_RUNTIME = "ark_runtime"
    PROCESS_RUNTIME = "process_runtime"
    BUILD_ARTIFACT = "build_artifact"
    GIT_INTERNAL = "git_internal"
    LOCAL_SECRET = "local_secret"
    LEGACY_OPERATIONAL = "legacy_operational"


class LCWorkKind(StrEnum):
    """Known LC-owned operational work families below ``work/``."""

    SOURCE_CORPUS_DRAFT = "source_corpus_draft"
    RESOURCE_DRAFT = "resource_draft"
    MATHLIB_CANDIDATE_CACHE = "mathlib_candidate_cache"
    SOURCE_CORPUS_PREVIEW = "source_corpus_preview"
    SOURCE_CORPUS_STAGING = "source_corpus_staging"
    SOURCE_INDEX_RECOVERY = "source_index_recovery"
    AUDIT = "audit"
    RECEIPT = "receipt"


@dataclass(frozen=True, slots=True)
class RepoPathClassification:
    """Classification result shared by publication, Release, and snapshots."""

    path: str
    path_class: RepoPathClass
    work_kind: LCWorkKind | None = None

    @property
    def publication_eligible(self) -> bool:
        return self.path_class is RepoPathClass.PORTABLE_TRUTH

    @property
    def release_eligible(self) -> bool:
        return self.path_class is RepoPathClass.PORTABLE_TRUTH

    @property
    def semantic_digest_eligible(self) -> bool:
        return self.path_class is RepoPathClass.PORTABLE_TRUTH

    @property
    def requires_migration(self) -> bool:
        return self.path_class is RepoPathClass.LEGACY_OPERATIONAL


_LEGACY_OPERATIONAL_PREFIXES = (
    (".lean_constellation", "source_draft"),
    (".lean_constellation", "resources", ".drafts"),
    (".lean_constellation", "resources", "tmp"),
    (".lean_constellation", "indexes", "mathlib_candidates.json"),
    (".lean_constellation", ".source_corpus_staging"),
    (".lean_constellation", "source_index", "operator_baseline.json"),
    (".lean_constellation", "audit", "gate_gaps.jsonl"),
    (".lean_constellation", "publication", "remote_receipts"),
    (".lean_constellation", "checkpoints"),
    (".lean_constellation", "locks"),
    (".lean_constellation", "staging"),
    ("docs", "lean-constellation", "public-api"),
)

_SPECIALIZED_RECOVERY_PREFIXES = (
    (".lean_constellation", "snapshots"),
    (".lean_constellation", ".locks"),
)

_BUILD_ROOTS = {
    ".lake",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "htmlcov",
}


def classify_repo_path(path: PurePosixPath) -> RepoPathClassification:
    """Classify one safe repository-relative POSIX path without filesystem I/O."""

    if not isinstance(path, PurePosixPath):
        raise TypeError("path must be a PurePosixPath")
    parts = path.parts
    if (
        path.is_absolute()
        or not parts
        or any(part in {"", ".", ".."} or "\\" in part or "\x00" in part for part in parts)
    ):
        raise ValueError("path must be a safe repository-relative POSIX path")

    normalized = path.as_posix()
    if any(_has_prefix(parts, prefix) for prefix in _LEGACY_OPERATIONAL_PREFIXES):
        return RepoPathClassification(normalized, RepoPathClass.LEGACY_OPERATIONAL)

    if parts[0] == ".git":
        return RepoPathClassification(normalized, RepoPathClass.GIT_INTERNAL)
    if parts[0] == ".agent_runtime":
        return RepoPathClassification(normalized, RepoPathClass.ARK_RUNTIME)
    if parts[0] == ".runtime":
        return RepoPathClassification(normalized, RepoPathClass.PROCESS_RUNTIME)
    if _is_local_secret(parts):
        return RepoPathClassification(normalized, RepoPathClass.LOCAL_SECRET)
    if parts[0] in _BUILD_ROOTS or "__pycache__" in parts or path.suffix == ".pyc":
        return RepoPathClassification(normalized, RepoPathClass.BUILD_ARTIFACT)

    if any(_has_prefix(parts, prefix) for prefix in _SPECIALIZED_RECOVERY_PREFIXES):
        return RepoPathClassification(normalized, RepoPathClass.SPECIALIZED_RECOVERY)

    work_prefix = (".lean_constellation", "work")
    if _has_prefix(parts, work_prefix):
        return _classify_lc_work(normalized, parts[len(work_prefix) :])

    return RepoPathClassification(normalized, RepoPathClass.PORTABLE_TRUTH)


def _classify_lc_work(path: str, relative_parts: tuple[str, ...]) -> RepoPathClassification:
    if not relative_parts:
        return RepoPathClassification(path, RepoPathClass.OPERATIONAL_WORK)

    lifecycle = relative_parts[0]
    if lifecycle == "drafts":
        work_kind = None
        if len(relative_parts) >= 2 and relative_parts[1] == "source_corpus":
            work_kind = LCWorkKind.SOURCE_CORPUS_DRAFT
        elif len(relative_parts) >= 2 and relative_parts[1] == "resources":
            work_kind = LCWorkKind.RESOURCE_DRAFT
        return RepoPathClassification(path, RepoPathClass.RECOVERABLE_WORK, work_kind)

    if lifecycle == "recovery":
        work_kind = (
            LCWorkKind.SOURCE_INDEX_RECOVERY
            if len(relative_parts) >= 2 and relative_parts[1] == "source_index"
            else None
        )
        return RepoPathClassification(path, RepoPathClass.RECOVERABLE_WORK, work_kind)

    if lifecycle == "cache":
        work_kind = (
            LCWorkKind.MATHLIB_CANDIDATE_CACHE
            if len(relative_parts) >= 2 and relative_parts[1] == "mathlib_candidates.json"
            else None
        )
        return RepoPathClassification(path, RepoPathClass.REBUILDABLE_WORK, work_kind)

    if lifecycle == "previews":
        work_kind = (
            LCWorkKind.SOURCE_CORPUS_PREVIEW
            if len(relative_parts) >= 2 and relative_parts[1] == "source_corpus"
            else None
        )
        return RepoPathClassification(path, RepoPathClass.REBUILDABLE_WORK, work_kind)

    if lifecycle == "staging":
        work_kind = (
            LCWorkKind.SOURCE_CORPUS_STAGING
            if len(relative_parts) >= 2 and relative_parts[1] == "source_corpus"
            else None
        )
        return RepoPathClassification(path, RepoPathClass.TRANSACTION_WORK, work_kind)

    if lifecycle == "audit":
        return RepoPathClassification(path, RepoPathClass.LOCAL_EVIDENCE, LCWorkKind.AUDIT)
    if lifecycle == "receipts":
        return RepoPathClassification(path, RepoPathClass.LOCAL_EVIDENCE, LCWorkKind.RECEIPT)
    return RepoPathClassification(path, RepoPathClass.OPERATIONAL_WORK)


def _has_prefix(parts: tuple[str, ...], prefix: tuple[str, ...]) -> bool:
    return parts[: len(prefix)] == prefix


def _is_local_secret(parts: tuple[str, ...]) -> bool:
    name = parts[-1]
    if name == "auth.json":
        return True
    return name == ".env" or (name.startswith(".env.") and name != ".env.example")


__all__ = [
    "LCWorkKind",
    "RepoPathClass",
    "RepoPathClassification",
    "classify_repo_path",
]
