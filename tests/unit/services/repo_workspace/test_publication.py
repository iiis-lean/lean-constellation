from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree

from lean_constellation.domain.publication import (
    PushPolicy,
    RemoteProfile,
    RepoPublicationBadge,
    RepoPublicationOverride,
    RepoPortability,
    RepoPublicationPresentation,
    WorkspacePublicationPolicy,
)
from lean_constellation.domain.refs import DeclRef
from lean_constellation.domain.repo import WorkspaceConfig
from lean_constellation.services.repo_workspace.publication import (
    PublicApiDocument,
    PublicApiDeclaration,
    RepoPublicationComponent,
)
from tests.unit_services_helpers import make_runtime
from tests.unit.flows.decl_round.test_decl_round_dependency_resolution import (
    _prepare_ready_adapter_provider,
)
from tests.unit.services.repo_workspace.test_repo_release import (
    _prepare_release_repo,
    _release,
    _set_contract_exports,
)


def test_managed_gitignore_preserves_user_content_and_is_idempotent(
    tmp_path: Path,
) -> None:
    runtime, _ = _prepare_release_repo(tmp_path)
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("custom-cache/\n", encoding="utf-8")

    first = runtime.repo_workspace.publication.refresh_managed_gitignore(tmp_path)
    first_bytes = gitignore.read_bytes()
    second = runtime.repo_workspace.publication.refresh_managed_gitignore(tmp_path)

    assert first.ok and first.value
    assert second.ok and not second.value
    assert gitignore.read_bytes() == first_bytes
    text = first_bytes.decode("utf-8")
    assert "custom-cache/" in text
    assert "/.agent_runtime/" in text
    assert "!/.env.example" in text


def test_publication_manifest_excludes_runtime_and_contains_no_absolute_paths(
    tmp_path: Path,
) -> None:
    runtime, _ = _prepare_release_repo(tmp_path)
    (tmp_path / "Main.lean").write_text("theorem ok : True := by trivial\n")
    (tmp_path / ".git" / "objects" / "aa").mkdir(parents=True)
    (tmp_path / ".git" / "objects" / "aa" / "object").write_bytes(b"git object")
    (tmp_path / ".runtime").mkdir()
    (tmp_path / ".runtime" / "server.json").write_text(
        json.dumps({"repo_root": str(tmp_path)})
    )
    (tmp_path / ".lean_constellation" / "snapshots").mkdir(parents=True)
    (
        tmp_path / ".lean_constellation" / "snapshots" / "snapshot.json"
    ).write_text("{}\n", encoding="utf-8")

    manifest = runtime.repo_workspace.publication.build_manifest(tmp_path)

    assert manifest.ok and manifest.value is not None
    assert manifest.value.schema_version == 2
    by_path = {entry.path: entry for entry in manifest.value.entries}
    assert by_path["Main.lean"].disposition == "include"
    assert {
        (entry.path, entry.recursive, entry.reason)
        for entry in manifest.value.excluded_directories
    } == {
        (".git", True, "runtime_or_git_state"),
        (
            ".lean_constellation/snapshots",
            True,
            "local_checkpoint_or_lock",
        ),
        (".runtime", True, "runtime_or_git_state"),
    }
    assert not any(
        entry.path.startswith(
            (
                ".git/",
                ".lean_constellation/snapshots/",
                ".runtime/",
            )
        )
        for entry in manifest.value.entries
    )
    payload = manifest.value.model_dump_json()
    assert str(tmp_path) not in payload


def test_publication_manifest_is_idempotent_and_excludes_itself(
    tmp_path: Path,
) -> None:
    runtime, _ = _prepare_release_repo(tmp_path)

    first = runtime.repo_workspace.publication.prepare_publication(tmp_path)
    assert first.ok, first.issues
    manifest_path = (
        tmp_path / ".lean_constellation/publication/manifest.json"
    )
    first_bytes = manifest_path.read_bytes()

    second = runtime.repo_workspace.publication.prepare_publication(tmp_path)

    assert second.ok, second.issues
    assert manifest_path.read_bytes() == first_bytes
    payload = json.loads(first_bytes)
    self_entries = [
        entry
        for entry in payload["entries"]
        if entry["path"]
        == ".lean_constellation/publication/manifest.json"
    ]
    assert self_entries == [
        {
            "disposition": "exclude",
            "path": ".lean_constellation/publication/manifest.json",
            "reason": "publication_manifest_self",
            "sha256": None,
            "size_bytes": 0,
        }
    ]


def test_publication_documents_are_portable_and_managed_readme_is_preserved(
    tmp_path: Path,
) -> None:
    runtime, _ = _prepare_release_repo(tmp_path)
    assert runtime.repo_workspace.metadata.ensure_repo_model(tmp_path).ok
    assert runtime.repo_workspace.metadata.set_repo_summary(
        tmp_path, summary="Internal release migration summary."
    ).ok
    readme = tmp_path / "README.md"
    readme.write_text("User preface.\n", encoding="utf-8")

    prepared = runtime.repo_workspace.publication.prepare_publication(
        tmp_path,
        presentation=RepoPublicationPresentation(
            title="Published result",
            description="Formalizes a public result.",
            topics=["Lean4", "formalization", "lean4"],
            badges=[
                RepoPublicationBadge(
                    label="source",
                    message="paper",
                    color="informational",
                )
            ],
            about_markdown="Independent formalization.",
            citation_markdown="Cite the source paper.",
            licensing_markdown="Released under a declared license.",
        ),
    )

    assert prepared.ok and prepared.value is not None, prepared.issues
    readme_text = readme.read_text(encoding="utf-8")
    assert "User preface." in readme_text
    assert (
        '<h1><img src="docs/lean-constellation/assets/'
        'lean-constellation-mark.svg"'
    ) in readme_text
    assert "Generated with <strong>Lean Constellation</strong>" in readme_text
    assert "Formalizes a public result." in readme_text
    assert "Internal release migration summary." not in readme_text
    assert "This repository exports **1 public declarations**" in readme_text
    assert (
        "[Public API index](docs/lean-constellation/PUBLIC_API.md)"
        in readme_text
    )
    assert (
        "[public boundary catalog]"
        "(docs/lean-constellation/PUBLIC_BOUNDARIES.md)"
        in readme_text
    )
    assert "| Declaration | Kind | Node | Status |" not in readme_text
    assert "topic-lean4" not in readme_text
    assert "Lean%20Constellation-generated" not in readme_text
    assert "img.shields.io/static/v1?" in readme_text
    assert "label=status&message=proved&color=0f8f88&style=flat-square" in readme_text
    assert "label=completion" not in readme_text
    assert "label=proofs" not in readme_text
    assert "label=source&message=paper&color=informational&style=flat-square" in readme_text
    assert (
        "[![LC: Lean Constellation]"
        "(https://img.shields.io/static/v1?label=LC&message=Lean+Constellation"
        "&color=092745&style=flat-square)]"
        "(https://github.com/iiis-lean/lean-constellation)"
    ) in readme_text
    assert (
        "[![MCP: Lean Toolkit]"
        "(https://img.shields.io/static/v1?label=MCP&message=Lean+Toolkit"
        "&color=e45132&style=flat-square)]"
        "(https://github.com/iiis-lean/lean-mcp-toolkit)"
    ) in readme_text
    assert readme_text.index("label=source") < readme_text.index("label=LC")
    assert readme_text.index("label=LC") < readme_text.index("label=MCP")
    assert "Current Lean Constellation Release" not in readme_text
    assert "Independent formalization." in readme_text
    assert prepared.value.topics == ["lean4", "formalization"]
    presentation = json.loads(
        (
            tmp_path / ".lean_constellation/publication/presentation.json"
        ).read_text()
    )
    assert presentation["description"] == "Formalizes a public result."
    api = json.loads(
        (tmp_path / "docs/lean-constellation/public-api.json").read_text()
    )
    assert {item["name"] for item in api["declarations"]} == {"PublicResult"}
    public_result = next(
        item for item in api["declarations"] if item["name"] == "PublicResult"
    )
    assert public_result["state"] == "proved"
    assert public_result["proof_available"] is True
    assert "trivial" in public_result["formal_code"]
    assert "sorry" not in public_result["formal_code"]
    assert public_result["statement_dependencies"] == [
        "current repo:Main.Foundation.Defs.Support"
    ]
    assert public_result["proof_dependencies"] == [
        "current repo:Main.Foundation.Defs.ProofHelper"
    ]
    api_markdown = (
        tmp_path / "docs/lean-constellation/PUBLIC_API.md"
    ).read_text()
    assert "## Dependency graph" in api_markdown
    assert "assets/public-api.svg" in api_markdown
    assert "Transitively implied edges are omitted" in api_markdown
    assert "| Node | Declaration | Kind | Status |" in api_markdown
    assert "theorem PublicResult" not in api_markdown
    assert "sorry" not in api_markdown
    assert "Support" not in api_markdown
    boundaries = json.loads(
        (
            tmp_path / "docs/lean-constellation/public-boundaries.json"
        ).read_text()
    )
    assert {
        item["declaration"]["name"] for item in boundaries["declarations"]
    } == {"ProofHelper", "PublicResult", "Support"}
    boundary_public_result = next(
        item
        for item in boundaries["declarations"]
        if item["declaration"]["name"] == "PublicResult"
    )
    assert boundary_public_result["main_export"] is True
    assert boundary_public_result["exported_scope_paths"] == ["Main"]
    assert all(
        not item["main_export"]
        for item in boundaries["declarations"]
        if item["declaration"]["name"] != "PublicResult"
    )
    boundaries_markdown = (
        tmp_path / "docs/lean-constellation/PUBLIC_BOUNDARIES.md"
    ).read_text()
    assert "assets/public-boundaries.svg" in boundaries_markdown
    assert "ProofHelper" in boundaries_markdown
    assert "Support" in boundaries_markdown
    declaration_pages = sorted(
        (
            tmp_path / "docs/lean-constellation/declarations"
        ).glob("*.md")
    )
    assert len(declaration_pages) == 3
    public_result_page = next(
        path for path in declaration_pages if "publicresult" in path.name
    )
    public_result_markdown = public_result_page.read_text()
    assert "No declaration summary is available." in public_result_markdown
    assert "## Statement dependencies" in public_result_markdown
    assert "## Proof dependencies" in public_result_markdown
    assert "theorem PublicResult" in public_result_markdown
    assert "trivial" in public_result_markdown
    assert "sorry" not in public_result_markdown
    assert prepared.value.declarations_dir == (
        "docs/lean-constellation/declarations"
    )
    assert prepared.value.public_boundaries_markdown_path == (
        "docs/lean-constellation/PUBLIC_BOUNDARIES.md"
    )
    stale_page = (
        tmp_path
        / "docs/lean-constellation/declarations/legacy-node-oldresult.md"
    )
    stale_page.write_text("obsolete\n")
    refreshed = runtime.repo_workspace.publication.prepare_publication(tmp_path)
    assert refreshed.ok, refreshed.issues
    assert not stale_page.exists()
    assert (
        tmp_path / "docs/lean-constellation/CITATION_TEMPLATE.cff"
    ).is_file()
    assert (
        tmp_path / "docs/lean-constellation/LICENSING_TEMPLATE.md"
    ).is_file()
    publication_mark = (
        tmp_path
        / "docs/lean-constellation/assets/lean-constellation-mark.svg"
    )
    assert publication_mark.is_file()
    assert publication_mark.read_bytes() == (
        (
            Path(__file__).resolve().parents[4]
            / "assets"
            / "lean-constellation-mark.svg"
        ).read_bytes()
    )
    for path in (
        tmp_path / "README.md",
        tmp_path / "docs/lean-constellation/public-api.json",
        tmp_path / "docs/lean-constellation/provenance.json",
        tmp_path / ".lean_constellation/publication/manifest.json",
    ):
        assert str(tmp_path) not in path.read_text(encoding="utf-8")
    for name in ("public-api.svg", "public-boundaries.svg"):
        svg_path = tmp_path / "docs/lean-constellation/assets" / name
        root = ElementTree.fromstring(svg_path.read_text(encoding="utf-8"))
        declaration_groups = [
            element
            for element in root.iter("{http://www.w3.org/2000/svg}g")
            if element.attrib.get("class") == "node"
            and element.attrib.get("id", "").startswith("decl-")
        ]
        dependency_groups = [
            element
            for element in root.iter("{http://www.w3.org/2000/svg}g")
            if element.attrib.get("class") == "edge"
            and element.attrib.get("id", "").startswith("dependency-")
        ]
        assert len(declaration_groups) == (1 if name == "public-api.svg" else 3)
        assert all(
            group.find("{http://www.w3.org/2000/svg}polygon") is not None
            for group in dependency_groups
        )
        for group in dependency_groups:
            edge_title = group.find("{http://www.w3.org/2000/svg}title")
            assert edge_title is not None and edge_title.text is not None
            consumer_endpoint, provider_endpoint = edge_title.text.split("->", 1)
            assert consumer_endpoint.endswith(":e")
            assert provider_endpoint.endswith(":w")
        svg_text = svg_path.read_text(encoding="utf-8")
        assert "Lean Constellation layout:" in svg_text
        assert "external API" not in svg_text
        assert "solid gray: Statement dependency" not in svg_text
        assert "Repository Public" not in "".join(root.itertext())


def test_adapter_publication_exposes_flat_api_and_immutable_upstream(
    tmp_path: Path,
) -> None:
    runtime = make_runtime()
    _prepare_ready_adapter_provider(runtime, tmp_path, bind_interface=True)

    prepared = runtime.repo_workspace.publication.prepare_publication(
        tmp_path,
        presentation=RepoPublicationPresentation(
            title="Adapter provider",
            description="A reviewed wrapper over an upstream Lean package.",
        ),
    )

    assert prepared.ok and prepared.value is not None, prepared.issues
    api = json.loads(
        (tmp_path / "docs/lean-constellation/public-api.json").read_text()
    )
    assert api["schema_version"] == 4
    assert api["repo_format"] == "adapter"
    assert [item["name"] for item in api["declarations"]] == ["main_result"]
    assert api["adapter_upstream"] == {
        "dependency_name": "upstream",
        "git_url": "https://example.invalid/upstream.git",
        "package_name": "upstream",
        "revision": "1" * 40,
        "source_kind": "git",
        "subdir": None,
        "trusted_build": True,
        "visible_modules": ["Upstream.Basic"],
    }
    boundaries = json.loads(
        (tmp_path / "docs/lean-constellation/public-boundaries.json").read_text()
    )
    assert boundaries["repo_format"] == "adapter"
    assert boundaries["declarations"][0]["exported_scope_paths"] == ["Main"]
    assert boundaries["declarations"][0]["main_export"] is True
    boundary_markdown = (
        tmp_path / "docs/lean-constellation/PUBLIC_BOUNDARIES.md"
    ).read_text()
    assert "flat committed `Main` public boundary" in boundary_markdown
    assert "Content-public declarations" not in boundary_markdown
    readme = (tmp_path / "README.md").read_text()
    assert "## Adapter upstream" in readme
    assert "https://example.invalid/upstream.git" in readme
    assert "`" + "1" * 40 + "`" in readme
    assert "flat committed Adapter Main boundary" in readme
    provenance = json.loads(
        (tmp_path / "docs/lean-constellation/provenance.json").read_text()
    )
    assert provenance["schema_version"] == 2
    assert provenance["repo_format"] == "adapter"
    assert provenance["adapter_upstream"] == api["adapter_upstream"]
    assert str(tmp_path) not in json.dumps(provenance)


def test_publication_status_badge_uses_proof_availability_and_flat_square(
    tmp_path: Path,
) -> None:
    (tmp_path / "lean-toolchain").write_text(
        "leanprover/lean4:v4.32.0\n",
        encoding="utf-8",
    )
    presentation = RepoPublicationPresentation()
    for availability, color in (("declared", "2563eb"), ("proved", "0f8f88")):
        api = PublicApiDocument(
            repo_key="Example",
            completion_mode="graph_proved",
            proof_availability=availability,
            summary="Example API.",
        )

        rendered = RepoPublicationComponent._render_badges(
            tmp_path,
            api=api,
            presentation=presentation,
        )

        assert (
            f"label=status&message={availability}&color={color}"
            "&style=flat-square"
        ) in rendered
        assert "label=completion" not in rendered
        assert "label=proofs" not in rendered
        assert (
            "label=Lean&message=4.32.0&color=6b4fbb&style=flat-square"
            in rendered
        )


def test_publication_tracks_scope_export_propagation_to_main(
    tmp_path: Path,
) -> None:
    runtime, _ = _prepare_release_repo(tmp_path)
    support = DeclRef(
        node="Main.Foundation.Defs", name="Support", revision=1
    )
    public_result = DeclRef(
        node="Main.Results", name="PublicResult", revision=1
    )
    _set_contract_exports(
        runtime,
        tmp_path,
        node_path="Main.Foundation",
        exports=[support],
    )
    _set_contract_exports(
        runtime,
        tmp_path,
        node_path="Main",
        exports=[public_result, support],
    )

    prepared = runtime.repo_workspace.publication.prepare_publication(tmp_path)

    assert prepared.ok, prepared.issues
    api = json.loads(
        (tmp_path / "docs/lean-constellation/public-api.json").read_text()
    )
    assert [item["name"] for item in api["declarations"]] == [
        "Support",
        "PublicResult",
    ]
    boundaries = json.loads(
        (
            tmp_path / "docs/lean-constellation/public-boundaries.json"
        ).read_text()
    )
    support_boundary = next(
        item
        for item in boundaries["declarations"]
        if item["declaration"]["name"] == "Support"
    )
    assert support_boundary["exported_scope_paths"] == [
        "Main.Foundation",
        "Main",
    ]
    assert support_boundary["main_export"] is True


def test_repo_publication_override_wins_over_workspace_defaults(
    tmp_path: Path,
) -> None:
    runtime, _ = _prepare_release_repo(tmp_path)

    resolved = runtime.repo_workspace.publication.resolve_policy(
        tmp_path,
        repo_override=RepoPublicationOverride(
            push_policy=PushPolicy.ON_RELEASE,
            canonical_fetch_url="https://example.invalid/example.git",
        ),
    )

    assert resolved.ok and resolved.value is not None
    assert resolved.value.policy.push_policy == PushPolicy.ON_RELEASE
    assert (
        resolved.value.policy.canonical_fetch_url
        == "https://example.invalid/example.git"
    )
    assert resolved.value.portability == RepoPortability.PORTABLE
    assert resolved.value.source_by_field["push_policy"] == "repo_override"


def test_workspace_remote_profile_derives_repo_neutral_canonical_urls(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "Mathematics"
    repo = workspace / "Uniform"
    repo.mkdir(parents=True)
    runtime = make_runtime(
        workspace_config=WorkspaceConfig(
            publication=WorkspacePublicationPolicy(
                repo_remote_profile="canonical",
                repo_remote_name_template="lc-{repo_key}",
                remote_profiles={
                    "canonical": RemoteProfile(
                        fetch_url_template=(
                            "https://git.example/{organization}/{repo_name}.git"
                        ),
                        push_url_template=(
                            "ssh://git@git.example/{organization}/{repo_name}.git"
                        ),
                        values={"organization": "formalizations"},
                    )
                },
            )
        )
    )
    assert runtime.repo_workspace.metadata.ensure_repo_model(repo).ok

    resolved = runtime.repo_workspace.publication.resolve_policy(repo)

    assert resolved.ok and resolved.value is not None, resolved.issues
    assert resolved.value.policy.canonical_fetch_url == (
        "https://git.example/formalizations/lc-Uniform.git"
    )
    assert resolved.value.policy.canonical_push_url == (
        "ssh://git@git.example/formalizations/lc-Uniform.git"
    )
    assert (
        resolved.value.source_by_field["canonical_fetch_url"]
        == "workspace_profile:canonical"
    )


def test_readme_lists_provider_release_completion_and_remote_source(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "Workspace"
    provider_root = workspace / "Provider"
    consumer_root = workspace / "Consumer"
    provider_root.mkdir(parents=True)
    consumer_root.mkdir()
    provider_runtime, provider_versions = _prepare_release_repo(provider_root)
    assert provider_runtime.repo_workspace.release.create_release(
        provider_root,
        release=_release("provider_r1", provider_versions),
    ).ok
    consumer_runtime, _ = _prepare_release_repo(consumer_root)
    assert consumer_runtime.repo_workspace.requirement.create_requirement(
        consumer_root,
        name="provider",
        target_repo="Provider",
        required_proof_availability="declared",
        reason="Use the provider API.",
    ).ok
    assert consumer_runtime.repo_workspace.requirement.mark_requirement_satisfied(
        consumer_root,
        requirement_name="provider",
        provider_repo="Provider",
        provider_release_id="provider_r1",
        provider_git_url="https://example.invalid/Provider.git",
    ).ok

    prepared = consumer_runtime.repo_workspace.publication.prepare_publication(
        consumer_root
    )

    assert prepared.ok and prepared.value is not None, prepared.issues
    readme = (consumer_root / "README.md").read_text()
    assert "| `declared` | `graph_declared` | `provider_r1` |" in readme
    assert "[remote](https://example.invalid/Provider.git)" in readme


def test_public_dependency_graph_is_consumer_first_and_transitively_reduced() -> None:
    def declaration(
        name: str,
        *,
        statement_dependencies: list[str] | None = None,
        proof_dependencies: list[str] | None = None,
    ) -> PublicApiDeclaration:
        return PublicApiDeclaration(
            name=name,
            revision=1,
            kind="theorem",
            node_path="Main.Results",
            module=f"Example.{name}",
            state="proved",
            status="committed",
            statement_dependencies=statement_dependencies or [],
            proof_dependencies=proof_dependencies or [],
        )

    base = declaration("Base")
    middle = declaration(
        "Middle",
        statement_dependencies=["current repo:Main.Results.Base"],
    )
    top = declaration(
        "Top",
        statement_dependencies=["current repo:Main.Results.Middle"],
        proof_dependencies=["current repo:Main.Results.Base"],
    )

    ordered = RepoPublicationComponent._ordered_public_api_declarations(
        [base, middle, top]
    )
    reduced = (
        RepoPublicationComponent._transitively_reduced_public_dependency_edges(
            ordered
        )
    )

    assert [item.name for item in ordered] == ["Top", "Middle", "Base"]
    assert {
        (consumer[1], provider[1], kind)
        for consumer, provider, kind in reduced
    } == {
        ("Top", "Middle", "Statement"),
        ("Middle", "Base", "Statement"),
    }


def test_public_dependency_graph_recursively_nests_scope_and_content_nodes() -> None:
    tree = SimpleNamespace(
        nodes=[
            SimpleNamespace(path="Main", parent_path=None),
            SimpleNamespace(path="Main.Algebra", parent_path="Main"),
            SimpleNamespace(
                path="Main.Algebra.Core",
                parent_path="Main.Algebra",
            ),
            SimpleNamespace(
                path="Main.Algebra.Results",
                parent_path="Main.Algebra",
            ),
            SimpleNamespace(path="Main.Top", parent_path="Main"),
        ]
    )

    def declaration(
        name: str,
        *,
        node_path: str,
        statement_dependencies: list[str] | None = None,
        proof_dependencies: list[str] | None = None,
    ) -> PublicApiDeclaration:
        return PublicApiDeclaration(
            name=name,
            revision=1,
            kind="theorem",
            node_path=node_path,
            module=f"Example.{name}",
            state="proved",
            status="committed",
            proof_available=True,
            statement_dependencies=statement_dependencies or [],
            proof_dependencies=proof_dependencies or [],
        )

    declarations = [
        declaration("Base", node_path="Main.Algebra.Core"),
        declaration(
            "Middle",
            node_path="Main.Algebra.Results",
            statement_dependencies=["current repo:Main.Algebra.Core.Base"],
        ),
        declaration(
            "Final",
            node_path="Main.Top",
            proof_dependencies=["current repo:Main.Algebra.Results.Middle"],
        ),
    ]
    svg = RepoPublicationComponent._render_public_boundary_svg(
        tree=tree,
        declarations=declarations,
        propagation={
            ("Main.Algebra.Core", "Base"): ["Main.Algebra", "Main"],
            ("Main.Algebra.Results", "Middle"): ["Main.Algebra", "Main"],
            ("Main.Top", "Final"): ["Main"],
        },
        title="Nested public boundary fixture",
    )

    root = ElementTree.fromstring(svg[svg.index("<svg") :])
    namespace = {"svg": "http://www.w3.org/2000/svg"}
    group_ids = {
        group.attrib.get("id")
        for group in root.findall(".//svg:g", namespace)
    }
    assert {
        "node-Main",
        "node-Main-Algebra",
        "node-Main-Algebra-Core",
        "node-Main-Algebra-Results",
        "node-Main-Top",
    }.issubset(group_ids)
    assert sum(
        identifier is not None and identifier.startswith("decl-")
        for identifier in group_ids
    ) == 3
    assert "external API" not in svg
    assert "solid gray: Statement dependency" not in svg
    assert "Nested public boundary fixture" not in "".join(root.itertext())
    assert "stroke-width=\"3\"" in svg
    assert "Lean Constellation layout:" in svg
