"""Shared workspace and runtime fixtures for Runtime Matrix tests."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from agent_runtime_kit.agent.provider_contracts import ProviderHomeSpec
from tests.real.lean_test_config import write_test_lean_toolchain
from tests.unit_services_helpers import valid_resource_readme

from lean_constellation.app import LeanAdminApi, create_test_control_runtime_services
from lean_constellation.domain.interface import DeclInterface, DeclKind
from lean_constellation.domain.preparation import RepoPreparationInput, SourceCorpusMode, UpstreamDependencyInput
from lean_constellation.domain.repo import RepoFormat
from lean_constellation.domain.repo_run import SourceScope
from lean_constellation.services.decl_graph import DeclState
from lean_constellation.services.external_clients import ExternalCommandResult, LeanCheckSummaryView


CONTENT_NODE_PATH = "Main.Topic.Core"


class RuntimeMatrixFakeLakeClient:
    """Small deterministic Lake client for scheduler tests that do not target real Lean."""

    def __init__(self) -> None:
        self.updated: list[Path] = []
        self.built: list[tuple[Path, str | None]] = []
        self.checked: list[tuple[Path, str]] = []
        self.snippets: list[tuple[Path, str]] = []
        self.lean_files: list[tuple[Path, str]] = []

    def run_lake_update(self, repo_root: Path) -> ExternalCommandResult:
        self.updated.append(Path(repo_root))
        return ExternalCommandResult(ok=True, command=["lake", "update"], cwd=str(repo_root), exit_code=0, summary="lake update ok")

    def run_lake_build(self, repo_root: Path, target: str | None = None) -> ExternalCommandResult:
        self.built.append((Path(repo_root), target))
        return ExternalCommandResult(
            ok=True,
            command=["lake", "build"] + ([target] if target else []),
            cwd=str(repo_root),
            exit_code=0,
            summary="lake build ok",
        )

    def run_minimal_import_check(self, repo_root: Path, module: str) -> LeanCheckSummaryView:
        self.checked.append((Path(repo_root), module))
        return LeanCheckSummaryView(ok=True, module=module, command=["lean"], summary=f"import {module} ok")

    def run_snippet_check(
        self,
        repo_root: Path,
        snippet: str | None = None,
        *,
        code: str | None = None,
        imports: list[str] | None = None,
        timeout_s: float | None = None,
        timeout_seconds: int | None = None,
    ) -> LeanCheckSummaryView:
        del imports, timeout_s, timeout_seconds
        checked = code if code is not None else (snippet or "")
        self.snippets.append((Path(repo_root), checked))
        return LeanCheckSummaryView(ok=True, command=["lean"], summary="snippet ok")

    def run_lake_env_lean(
        self,
        *,
        repo_root: Path,
        rel_file: str,
        json: bool = True,
        timeout_seconds: int | None = None,
    ) -> ExternalCommandResult:
        del json, timeout_seconds
        self.lean_files.append((Path(repo_root), rel_file))
        return ExternalCommandResult(
            ok=True,
            command=["lake", "env", "lean", "--json", rel_file],
            cwd=str(repo_root),
            exit_code=0,
            stdout_excerpt="",
            stderr_excerpt="",
            summary="lake env lean ok",
        )

    def summarize_command_result(self, result: ExternalCommandResult):
        from lean_constellation.services.external_clients import LakeCommandSummaryView

        return LakeCommandSummaryView(
            ok=result.ok,
            command=result.command,
            summary=result.summary or "",
            exit_code=result.exit_code,
            timed_out=result.timed_out,
            stderr_excerpt=result.stderr_excerpt,
        )


@dataclass
class ResourceFixture:
    local_file: Path
    web_url: str
    arxiv_id: str


@dataclass
class DeclRoundFixture:
    node_path: str
    decl_name: str
    strategy_id: str
    round_id: str
    round_index: int


@dataclass
class RuntimeMatrixWorkspace:
    tmp_path: Path
    runtime_root: Path
    workspace_root: Path
    provider_repo: Path
    consumer_repo: Path
    adapter_repo: Path
    upstream_repo: Path
    resource_root: Path
    runtime: object
    admin: LeanAdminApi
    lake: object
    resources: ResourceFixture
    live_toolkit_base_url: str | None
    live_toolkit_visible_repo: Path | None
    mathlib_template_root: Path | None

    def create_home(
        self,
        agent_type: str,
        *,
        provider_type: str = "scripted",
        home_id: str | None = None,
    ) -> None:
        self.runtime.ark.agent_service.home_service.create_home(
            ProviderHomeSpec(provider_type=provider_type, home_id=home_id or agent_type)
        )

    def create_homes(self, *agent_types: str, provider_type: str = "scripted") -> None:
        for agent_type in agent_types:
            self.create_home(agent_type, provider_type=provider_type)

    def write_bootstrap_preparation(self, repo_root: Path | None = None) -> None:
        repo_root = repo_root or self.provider_repo
        repo_root.mkdir(parents=True, exist_ok=True)
        assert self.runtime.repo_workspace.metadata.ensure_repo_model(repo_root).ok
        written = self.runtime.repo_workspace.preparation.write_preparation_input(
            repo_root,
            input=RepoPreparationInput(
                goal="Provide a small dependency for the consumer repo.",
                source_corpus_mode=SourceCorpusMode.PREPARE,
                requirement_refs=[{"consumer_repo": self.consumer_repo.name, "requirement_name": "need_provider"}],
            ),
        )
        assert written.ok, written.issues

    def prepare_provider_native_repo(self, *, allow_interface_supplement: bool = False) -> None:
        self.write_bootstrap_preparation(self.provider_repo)
        source_root = self.provider_repo / ".lean_constellation" / "source"
        source_root.mkdir(parents=True, exist_ok=True)
        (source_root / "README.md").write_text(
            "Runtime Matrix source corpus.\n"
            "Source provenance: local strict Runtime Matrix fixture.\n"
            "Reading order: read source.md after this README entry.\n"
            "Main material: source.md contains the theorem fixture.\n"
            "Known gaps and extraction limits: no missing source sections are known.\n",
            encoding="utf-8",
        )
        (source_root / "source.md").write_text(
            "Runtime Matrix source\n"
            "Source provenance: local strict Runtime Matrix fixture.\n"
            "Reading order: read this source.md entry as the main material.\n"
            "The main theorem is `main_result : True`.\n"
            "The proof is by triviality and may use a Mathlib hint.\n"
            "Known gaps and extraction limits: no missing source sections are known.\n",
            encoding="utf-8",
        )
        written = self.runtime.repo_workspace.preparation.write_preparation_input(
            self.provider_repo,
            input=RepoPreparationInput(
                goal="Formalize a tiny true theorem.",
                source_corpus_mode=SourceCorpusMode.EXISTING,
                source_corpus_relpath=".lean_constellation/source",
                requirement_refs=[{"consumer_repo": self.consumer_repo.name, "requirement_name": "need_provider"}],
                interface_inputs=[
                    DeclInterface(name="main_result", kind=DeclKind.THEOREM, summary="Expose the main true theorem.")
                ],
                allow_interface_supplement=allow_interface_supplement,
            ),
        )
        assert written.ok, written.issues
        initialized = self.runtime.repo_workspace.initialize_repo_as_native(self.provider_repo, project_name=self.provider_repo.name)
        assert initialized.ok, initialized.issues

    def prepare_provider_ready_repo(self) -> None:
        self.provider_repo.mkdir(parents=True, exist_ok=True)
        assert self.runtime.repo_workspace.metadata.ensure_repo_model(self.provider_repo).ok
        written = self.runtime.repo_workspace.preparation.write_preparation_input(
            self.provider_repo,
            input=RepoPreparationInput(
                goal="Runtime Matrix ready repo.",
                source_corpus_mode=SourceCorpusMode.EXISTING,
                source_corpus_relpath=".lean_constellation/source",
                interface_inputs=[],
            ),
        )
        assert written.ok, written.issues
        source_root = self.provider_repo / ".lean_constellation" / "source"
        source_root.mkdir(parents=True, exist_ok=True)
        (source_root / "README.md").write_text(
            "Runtime Matrix source corpus.\n"
            "Source provenance: local strict Runtime Matrix fixture.\n"
            "Reading order: read source.md after this README entry.\n"
            "Main material: source.md contains the theorem fixture.\n"
            "Known gaps and extraction limits: no missing source sections are known.\n",
            encoding="utf-8",
        )
        (source_root / "source.md").write_text(
            "Runtime Matrix source.\n"
            "Source provenance: local strict Runtime Matrix fixture.\n"
            "Reading order: read this source.md entry as the main material.\n"
            "The theorem is True.\n"
            "The proof is by triviality.\n"
            "Known gaps and extraction limits: no missing source sections are known.\n",
            encoding="utf-8",
        )
        initialized = self.runtime.repo_workspace.initialize_repo_as_native(self.provider_repo, project_name=self.provider_repo.name)
        assert initialized.ok, initialized.issues
        self._prepare_minimal_source_index(path="source.md")
        assert self.runtime.node.ensure_native_root_main_contract(self.provider_repo).ok
        committed = self.runtime.node.commit_scope_contract(self.provider_repo, scope_path="Main", summary="Main scope complete.")
        assert committed.ok, committed.issues
        refreshed = self.runtime.lean_projection.refresh_node_projection(self.provider_repo, node_path="Main")
        assert refreshed.ok, refreshed.issues

    def prepare_adapter_truth(self) -> None:
        self.adapter_repo.mkdir(parents=True, exist_ok=True)
        assert self.runtime.repo_workspace.metadata.ensure_repo_model(self.adapter_repo).ok
        written = self.runtime.repo_workspace.preparation.write_preparation_input(
            self.adapter_repo,
            input=RepoPreparationInput(
                goal="Expose an upstream theorem as an adapter provider.",
                source_corpus_mode=SourceCorpusMode.NONE,
                source_corpus_relpath=None,
                interface_inputs=[
                    DeclInterface(name="main_result", kind=DeclKind.THEOREM, summary="Expose upstream smoke theorem.")
                ],
            ),
        )
        assert written.ok, written.issues
        initialized = self.runtime.repo_workspace.initialize_repo_as_adapter(
            self.adapter_repo,
            upstream=UpstreamDependencyInput(
                git_url=str(self.upstream_repo),
                package_name="upstream",
                module_name="Upstream",
                evidence_summary="Local runtime matrix upstream fixture.",
            ),
            project_name=self.adapter_repo.name,
        )
        assert initialized.ok, initialized.issues
        root_contract = self.runtime.node.ensure_adapter_root_main_contract(self.adapter_repo)
        assert root_contract.ok, root_contract.issues
        upstream = self.runtime.adapter.write_adapter_upstream_metadata(
            self.adapter_repo,
            source_kind="local_path",
            local_path=str(self.upstream_repo),
            package_name="upstream",
            dependency_name="upstream",
            evidence_summary="Local runtime matrix upstream fixture.",
            visible_modules=["Upstream"],
        )
        assert upstream.ok, upstream.issues
        trusted = self.runtime.adapter.mark_upstream_build_trusted(
            self.adapter_repo,
            summary="Runtime Matrix local upstream fixture is trusted.",
        )
        assert trusted.ok, trusted.issues

    def allocate_resource_branch_draft(self, *, target_kind: str, target: str) -> str:
        prepared_target = self.runtime.material.prepare_resource_target(
            target_kind=target_kind,
            target=target,
        )
        assert prepared_target.ok and prepared_target.value is not None, prepared_target.issues
        draft = self.runtime.material.allocate_resource_draft(
            self.provider_repo,
            target=prepared_target.value,
            title_hint="Runtime Matrix branch resource",
        )
        assert draft.ok and draft.value is not None, draft.issues
        _write_resource_draft_files(Path(draft.value.draft_root), "runtime matrix resource text\n")
        return draft.value.draft.draft_id

    def fill_resource_draft(self, draft_id: str, text: str = "runtime matrix resource text\n") -> None:
        draft = self.runtime.material.get_resource_draft(self.provider_repo, draft_id=draft_id)
        assert draft.ok and draft.value is not None, draft.issues
        _write_resource_draft_files(Path(draft.value.draft_root), text)

    def create_active_resource(self, *, target_kind: str, target: str) -> str:
        draft_id = self.allocate_resource_branch_draft(target_kind=target_kind, target=target)
        prepared_target = self.runtime.material.prepare_resource_target(
            target_kind=target_kind,
            target=target,
        )
        assert prepared_target.ok and prepared_target.value is not None, prepared_target.issues
        promoted = self.runtime.material.submit_local_resource_created(
            self.provider_repo,
            target=prepared_target.value,
            draft_id=draft_id,
            summary="Runtime Matrix duplicate fixture resource.",
            classification_reason="The fixture is supporting evidence owned by the current repository.",
            resource_role="Provide deterministic duplicate-detection coverage.",
            consumer_formalization_scope="The current repository retains all formal theorem ownership.",
        )
        assert promoted.ok and promoted.value is not None, promoted.issues
        assert promoted.value.resource_key is not None
        return promoted.value.resource_key

    def setup_content_node(self, *, repo_root: Path | None = None, node_path: str = CONTENT_NODE_PATH) -> None:
        repo_root = repo_root or self.provider_repo
        assert self.runtime.node.node_tree.ensure_root_scope_node(repo_root).ok
        parts = node_path.split(".")
        for index in range(2, len(parts)):
            scope_path = ".".join(parts[:index])
            created_scope = self.runtime.node.create_scope_node(
                repo_root,
                path=scope_path,
                goal=f"{scope_path} goal.",
                boundary=f"{scope_path} boundary.",
            )
            if not created_scope.ok:
                assert any(issue.kind in {"node_path_exists", "node_already_exists"} for issue in created_scope.issues), created_scope.issues
        created = self.runtime.node.create_content_node(
            repo_root,
            path=node_path,
            goal="Runtime Matrix content goal.",
            boundary="Runtime Matrix content boundary.",
            objective="Build a small true declaration.",
            success_criteria="The content node reaches a terminal state.",
        )
        if not created.ok:
            assert any(issue.kind == "node_path_exists" for issue in created.issues), created.issues

    def create_decl_round(
        self,
        *,
        repo_root: Path | None = None,
        node_path: str = CONTENT_NODE_PATH,
        decl_name: str = "main_result",
        kind: str = "theorem",
        target_state: DeclState = DeclState.PROVED,
        public: bool = False,
    ) -> DeclRoundFixture:
        repo_root = repo_root or self.provider_repo
        self.setup_content_node(repo_root=repo_root, node_path=node_path)
        strategy = self.runtime.decl_graph.ensure_open_strategy(
            repo_root,
            node_path=node_path,
            objective="Runtime Matrix declaration strategy.",
        )
        assert strategy.ok and strategy.value is not None, strategy.issues
        round_record = self.runtime.decl_graph.create_round_draft(
            repo_root,
            node_path=node_path,
            strategy_id=strategy.value.strategy_id,
            objective="Runtime Matrix declaration round.",
        )
        assert round_record.ok and round_record.value is not None, round_record.issues
        created = self.runtime.decl_graph.create_decl(
            repo_root,
            node_path=node_path,
            round_id=round_record.value.round_id,
            name=decl_name,
            kind=kind,
            objective=f"Create {decl_name}.",
            summary=f"{decl_name} summary.",
            public=public,
            target_state=target_state,
        )
        assert created.ok, created.issues
        return DeclRoundFixture(
            node_path=node_path,
            decl_name=decl_name,
            strategy_id=strategy.value.strategy_id,
            round_id=round_record.value.round_id,
            round_index=round_record.value.round_index,
        )

    def _prepare_minimal_source_index(self, *, path: str) -> None:
        material = self.runtime.material
        resolved = material.resolve_source_scope(self.provider_repo, source_scope=SourceScope(mode="all"))
        assert resolved.ok and resolved.value is not None, resolved.issues
        opened = material.open_source_index_update(
            self.provider_repo,
            resolved_scope=resolved.value,
            index_policy="auto",
        )
        assert opened.ok and opened.value is not None, opened.issues
        assert material.set_source_index_overview(
            self.provider_repo,
            overview="Runtime Matrix source index.",
        ).ok
        assert material.create_source_block(
            self.provider_repo,
            parent_id="root",
            kind="theorem",
            title="Runtime Matrix theorem source",
            summary="A small source block supporting the runtime matrix theorem.",
            subtype=None,
        ).ok
        assert material.add_source_block_ref(
            self.provider_repo,
            block_id="b_0001",
            path=path,
            start_line=1,
            end_line=3,
            role="main",
        ).ok
        for block_id in ("b_0001", "root"):
            assert material.mark_block_refs_done(
                self.provider_repo, block_id=block_id
            ).ok
            assert material.mark_block_links_done(
                self.provider_repo, block_id=block_id
            ).ok
            assert material.mark_block_completed(
                self.provider_repo, block_id=block_id
            ).ok
        assert material.set_file_survey_status(
            self.provider_repo,
            path=path,
            status="surveyed",
            summary="Read in full.",
        ).ok
        assert material.set_file_indexing_status(
            self.provider_repo, path=path, status="indexed"
        ).ok
        readme_path = self.provider_repo / ".lean_constellation" / "source" / "README.md"
        if path != "README.md" and readme_path.exists():
            assert material.set_file_survey_status(
                self.provider_repo,
                path="README.md",
                status="skipped",
                summary="Entry file only; source.md contains the indexed material.",
            ).ok
            assert material.set_file_indexing_status(
                self.provider_repo,
                path="README.md",
                status="skipped",
            ).ok
        assert material.validate_source_index(self.provider_repo).ok
        validated = material.validate_source_index_update(
            self.provider_repo,
            baseline_index=None,
            expected_baseline_digest=opened.value.baseline_digest,
            resolved_scope=resolved.value.resolved_file_paths,
            require_completed=True,
        )
        assert validated.ok and validated.value is not None, validated.issues
        assert validated.value.gate.passed, validated.value.gate.issues
        committed = material.commit_source_index_update(
            self.provider_repo,
            validated=validated.value,
        )
        assert committed.ok, committed.issues


@pytest.fixture
def runtime_matrix_workspace(tmp_path: Path) -> RuntimeMatrixWorkspace:
    return create_runtime_matrix_workspace(tmp_path)


def create_runtime_matrix_workspace(
    tmp_path: Path,
    *,
    lake_client: object | None = None,
    initialize_provider_format: bool = True,
) -> RuntimeMatrixWorkspace:
    runtime_root = tmp_path / ".agent_runtime"
    workspace_root = tmp_path / "workspace"
    provider_repo = workspace_root / "Provider"
    consumer_repo = workspace_root / "Consumer"
    adapter_repo = workspace_root / "Adapter"
    upstream_repo = workspace_root / "Upstream"
    resource_root = workspace_root / "resources"
    for path in (provider_repo, consumer_repo, adapter_repo, upstream_repo, resource_root):
        path.mkdir(parents=True, exist_ok=True)

    _write_minimal_lake_repo(
        provider_repo,
        module_name=provider_repo.name,
        node_path=CONTENT_NODE_PATH,
    )
    _write_consumer_repo(consumer_repo)
    _write_upstream_repo(upstream_repo)
    resources = _write_resource_fixture(resource_root)
    lake = lake_client or RuntimeMatrixFakeLakeClient()
    runtime = create_test_control_runtime_services(
        runtime_root=runtime_root,
        external_overrides={"lake": lake},
        max_concurrent_flow_advances=1,
        max_concurrent_steps=1,
        start_paused=True,
    )
    from tests.real.runtime_matrix.scripted_provider import get_or_install_scripted_provider

    get_or_install_scripted_provider(runtime)
    if initialize_provider_format:
        initialized_provider = runtime.repo_workspace.metadata.set_repo_format(
            provider_repo,
            repo_format=RepoFormat.NATIVE,
            reason="Runtime Matrix fixture provides an existing minimal native Lake project.",
        )
        assert initialized_provider.ok, initialized_provider.issues
    return RuntimeMatrixWorkspace(
        tmp_path=tmp_path,
        runtime_root=runtime_root,
        workspace_root=workspace_root,
        provider_repo=provider_repo,
        consumer_repo=consumer_repo,
        adapter_repo=adapter_repo,
        upstream_repo=upstream_repo,
        resource_root=resource_root,
        runtime=runtime,
        admin=LeanAdminApi(runtime),
        lake=lake,
        resources=resources,
        live_toolkit_base_url=os.environ.get("LEAN_CONSTELLATION_REAL_TOOLKIT_BASE_URL"),
        live_toolkit_visible_repo=_env_path("LEAN_CONSTELLATION_REAL_ADAPTER_UPSTREAM_REPO"),
        mathlib_template_root=_mathlib_template_root(),
    )


def _write_minimal_lake_repo(
    repo_root: Path,
    *,
    module_name: str,
    node_path: str = "Topic.Core",
) -> None:
    node_module = f"{module_name}.{node_path}"
    repo_root.mkdir(parents=True, exist_ok=True)
    write_test_lean_toolchain(repo_root)
    (repo_root / "lakefile.toml").write_text(
        f'name = "{repo_root.name}"\n'
        'version = "0.1.0"\n'
        f'defaultTargets = ["{module_name}"]\n\n'
        "[[lean_lib]]\n"
        f'name = "{module_name}"\n',
        encoding="utf-8",
    )
    (repo_root / f"{module_name}.lean").write_text(
        f"import {node_module}.Prelude\n"
        f"import {node_module}.Interfaces\n",
        encoding="utf-8",
    )
    node_root = repo_root / module_name
    for segment in node_path.split("."):
        node_root /= segment
    prelude = node_root / "Prelude.lean"
    prelude.parent.mkdir(parents=True, exist_ok=True)
    prelude.write_text(
        f"namespace {node_module}\n\n"
        "def seedNat : Nat := 1\n\n"
        "theorem seedTrue : True := by\n"
        "  trivial\n\n"
        f"end {node_module}\n",
        encoding="utf-8",
    )
    interfaces = node_root / "Interfaces.lean"
    interfaces.write_text(
        f"import {node_module}.Prelude\n\n"
        f"namespace {node_module}\n\n"
        "theorem interfaceTrue : True := by\n"
        "  trivial\n\n"
        f"end {node_module}\n",
        encoding="utf-8",
    )


def _write_consumer_repo(repo_root: Path) -> None:
    _write_minimal_lake_repo(repo_root, module_name="Consumer")
    (repo_root / "README.md").write_text("Consumer repo that requires Provider.need_provider.\n", encoding="utf-8")


def _write_upstream_repo(repo_root: Path) -> None:
    repo_root.mkdir(parents=True, exist_ok=True)
    write_test_lean_toolchain(repo_root)
    (repo_root / "lakefile.toml").write_text(
        'name = "Upstream"\n'
        'version = "0.1.0"\n'
        'defaultTargets = ["Upstream"]\n\n'
        "[[lean_lib]]\n"
        'name = "Upstream"\n',
        encoding="utf-8",
    )
    (repo_root / "Upstream.lean").write_text(
        "/-- Upstream smoke theorem docstring used by runtime matrix tests. -/\n"
        "theorem upstreamSmoke : True := by\n"
        "  trivial\n\n"
        "/-- Addition by zero on Nat, used to test theorem extraction. -/\n"
        "theorem upstreamAddZero (n : Nat) : n + 0 = n := by\n"
        "  simp\n",
        encoding="utf-8",
    )
    if shutil.which("git"):
        subprocess.run(["git", "init"], cwd=repo_root, check=True, stdout=subprocess.DEVNULL)
        subprocess.run(["git", "add", "."], cwd=repo_root, check=True, stdout=subprocess.DEVNULL)
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Runtime Matrix",
                "-c",
                "user.email=runtime-matrix@example.invalid",
                "commit",
                "-m",
                "init upstream fixture",
            ],
            cwd=repo_root,
            check=True,
            stdout=subprocess.DEVNULL,
        )


def _write_resource_fixture(resource_root: Path) -> ResourceFixture:
    local_file = resource_root / "local_note.md"
    local_file.write_text(
        "# Local Runtime Matrix Note\n\nThe local note supports the tiny theorem `True`.\n",
        encoding="utf-8",
    )
    return ResourceFixture(
        local_file=local_file,
        web_url="https://example.com/runtime-matrix-resource",
        arxiv_id="2501.12345",
    )


def _write_resource_draft_files(draft_root: Path, text: str) -> None:
    (draft_root / "README.md").write_text(
        valid_resource_readme(title="Runtime Matrix resource"),
        encoding="utf-8",
    )
    (draft_root / "original" / "raw.txt").parent.mkdir(parents=True, exist_ok=True)
    (draft_root / "original" / "raw.txt").write_text(text, encoding="utf-8")
    (draft_root / "normalized" / "main.md").parent.mkdir(parents=True, exist_ok=True)
    (draft_root / "normalized" / "main.md").write_text(text, encoding="utf-8")


def _env_path(name: str) -> Path | None:
    value = os.environ.get(name)
    if not value:
        return None
    return Path(value).expanduser()


def _mathlib_template_root() -> Path | None:
    explicit = _env_path("LEAN_CONSTELLATION_REAL_LEAN_TEMPLATE_ROOT") or _env_path(
        "LEAN_CONSTELLATION_LOCAL_LAKE_CACHE_PROJECT_ROOT"
    )
    if explicit is not None:
        return explicit
    return None
