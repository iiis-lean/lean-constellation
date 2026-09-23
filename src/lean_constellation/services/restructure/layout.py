"""Standard physical project layout for Restructure output."""

from __future__ import annotations

from pathlib import Path

from lean_constellation.domain.restructure import DeclKind, NodeKind, RepoPlan, WorkspacePlan, safe_module_segment
from lean_constellation.services.restructure.store import RestructureStore

_KIND_DIR = {
    DeclKind.DEF: "Defs",
    DeclKind.ABBREV: "Defs",
    DeclKind.STRUCTURE: "Types",
    DeclKind.CLASS: "Types",
    DeclKind.INDUCTIVE: "Types",
    DeclKind.INSTANCE: "Instances",
    DeclKind.NOTATION: "Defs",
    DeclKind.MACRO: "Defs",
    DeclKind.LEMMA: "Lemmas",
    DeclKind.THEOREM: "Theorems",
    DeclKind.COROLLARY: "Theorems",
    DeclKind.SUPPORT: "Defs",
}


class RestructureLayout:
    """Create standard directories/templates without Native business truth."""

    def __init__(self, store: RestructureStore) -> None:
        self.store = store

    def prepare_workspace(self, plan: WorkspacePlan) -> None:
        self.store.root.mkdir(parents=True, exist_ok=True)
        for repo in plan.repos.values():
            self.prepare_repo(repo.directory, repo.module_root, repo.plan)

    def prepare_repo(self, directory: str, module_root: str, plan: RepoPlan | None = None) -> Path:
        root = self.store.repo_root(directory)
        module = safe_module_segment(module_root, label="module root")
        main_root = root / module / "Main"
        main_root.mkdir(parents=True, exist_ok=True)
        (root / ".lean_constellation" / "restructure").mkdir(parents=True, exist_ok=True)
        self._create_if_missing(main_root / "Prelude.lean", f"/- Prelude for {module}.Main -/\n")
        self._create_if_missing(main_root / "Interfaces.lean", f"/- Interfaces for {module}.Main -/\n")
        if plan is not None:
            for node in plan.nodes.values():
                node_dir = self.node_root(directory, module_root, node.path)
                node_dir.mkdir(parents=True, exist_ok=True)
                self._create_if_missing(node_dir / "Prelude.lean", f"/- Prelude for {module}.{node.path} -/\n")
                self._create_if_missing(node_dir / "Interfaces.lean", f"/- Interfaces for {module}.{node.path} -/\n")
        return root

    def node_root(self, directory: str, module_root: str, node_path: str) -> Path:
        """Return the physical directory for a logical Main/Scope/Content node."""

        parts = [part for part in node_path.split(".") if part]
        if not parts or parts[0] != "Main":
            raise ValueError("node path must be rooted at Main")
        for part in parts:
            safe_module_segment(part, label="node segment")
        root = self.store.repo_root(directory) / safe_module_segment(module_root, label="module root") / "Main"
        return root.joinpath(*parts[1:])

    def decl_file(self, directory: str, module_root: str, node_path: str, kind: DeclKind, name: str) -> Path:
        safe_module_segment(name, label="declaration name")
        root = self.node_root(directory, module_root, node_path) / _KIND_DIR[kind]
        root.mkdir(parents=True, exist_ok=True)
        return root / f"{name}.lean"

    def node_module(self, module_root: str, node_path: str) -> str:
        safe_module_segment(module_root, label="module root")
        parts = node_path.split(".")
        if not parts or parts[0] != "Main":
            raise ValueError("node path must be rooted at Main")
        return ".".join([module_root, *parts])

    @staticmethod
    def decl_template(kind: DeclKind, lean_name: str) -> str:
        """Return an intentionally small editable template for one declaration."""

        if kind in {DeclKind.THEOREM, DeclKind.LEMMA, DeclKind.COROLLARY}:
            body = f"theorem {lean_name} : True :=\n-- LC proof begin\nby\n  sorry\n-- LC proof end\n"
        elif kind is DeclKind.DEF or kind is DeclKind.ABBREV:
            body = f"def {lean_name} : Nat := 0\n"
        elif kind in {DeclKind.STRUCTURE, DeclKind.CLASS}:
            body = f"structure {lean_name} where\n"
        elif kind is DeclKind.INDUCTIVE:
            body = f"inductive {lean_name} where\n"
        elif kind is DeclKind.INSTANCE:
            body = f"instance : True := True.intro\n-- replace with the registered instance {lean_name}\n"
        elif kind is DeclKind.NOTATION:
            body = f"notation \"{lean_name}\" => True\n"
        elif kind is DeclKind.MACRO:
            body = f"-- macro {lean_name} (fill in the syntax and expansion)\n"
        else:
            body = f"-- support declaration {lean_name}\n"
        return f"/- LC restructure declaration: {lean_name} -/\n{body}"

    @staticmethod
    def _create_if_missing(path: Path, content: str) -> None:
        if not path.exists():
            path.write_text(content, encoding="utf-8")


def decl_kind_dir(kind: DeclKind) -> str:
    return _KIND_DIR[kind]


__all__ = ["RestructureLayout", "decl_kind_dir"]


def refresh_projections(store: RestructureStore, directory: str, owned_node: str | None = None) -> None:
    """Refresh registered imports under the workspace metadata writer lock."""
    from lean_constellation.domain.restructure import ContentWork
    from lean_constellation.services.restructure.source_contract import with_imports
    workspace, _ = store.load_workspace_plan()
    if workspace is None:
        return
    repo_key = workspace.repo_key_for_directory(directory)
    repo = workspace.repos[repo_key]
    if repo.plan is None:
        return
    catalogue = {}
    works = {}
    for key, spec in workspace.repos.items():
        if spec.plan is None:
            continue
        for node in spec.plan.content_nodes():
            work, _ = store.load_content(spec.directory, node.path, ContentWork)
            if work:
                works[key, node.path] = work
                for decl in work.decls.values():
                    catalogue[key, node.path, decl.name] = decl

    def resolve(ref):
        matches = [d for (r, n, name), d in catalogue.items()
                   if r == ref.repo and name == ref.name and (ref.node is None or n == ref.node)]
        return matches[0] if len(matches) == 1 else None

    def module(decl):
        return Path(decl.file).with_suffix("").as_posix().replace("/", ".")

    def write(path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() or path.read_text() != text:
            path.write_text(text, encoding="utf-8")

    root = store.repo_root(directory)
    selected = list(repo.plan.main_exports) + list(repo.plan.interface_seeds)
    for work in works.values():
        selected.extend(dep.ref for d in work.decls.values() for dep in d.dependencies if not dep.external)
    for node in repo.plan.nodes.values():
        selected.extend(node.exports)
    layout = RestructureLayout(store)
    for (key, node_path), work in works.items():
        if key != repo_key or (owned_node is not None and node_path != owned_node):
            continue
        for decl in work.decls.values():
            # Declaration files import their node Prelude.  The Prelude owns
            # the package surface, while explicit local dependencies remain
            # direct imports of their declaration modules.
            modules = [layout.node_module(repo.module_root, node_path) + ".Prelude"] + [module(other) for dep in decl.dependencies if not dep.external
                       if (other := resolve(dep.ref)) is not None and other is not decl]
            path = root / decl.file
            if path.is_file():
                write(path, with_imports(path.read_text(), modules))
    for node in repo.plan.nodes.values():
        refs = repo.plan.main_exports if node.kind is NodeKind.MAIN else selected
        modules = []
        for ref in refs:
            decl = resolve(ref)
            if decl is None or ref.repo != repo_key:
                continue
            owner = next((n for (r, n, name), d in catalogue.items() if r == repo_key and d is decl), None)
            if node.kind is NodeKind.MAIN or owner == node.path or (owner and owner.startswith(node.path + ".")):
                modules.append(module(decl))
        node_root = layout.node_root(directory, repo.module_root, node.path)
        write(node_root / "Interfaces.lean", "-- Generated LC interface projection\n" + "".join(f"import {m}\n" for m in sorted(set(modules))))
        # Keep the broad package import at one node-level boundary for now.
        # External declaration-to-module resolution can later replace it with
        # a minimal Mathlib module set without changing declaration files.
        deps = ["Mathlib"]
        for dependency in node.dependencies:
            deps.append(layout.node_module(repo.module_root, dependency) + ".Interfaces")
        write(node_root / "Prelude.lean", "-- Generated LC dependency projection\n" + "".join(f"import {m}\n" for m in sorted(set(deps))))
    write(root / f"{repo.module_root}.lean", f"import {repo.module_root}.Main.Interfaces\n")
