"""Lean Hierarchical Formalization: mathematical metadata, without build history."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, AfterValidator, model_validator


def safe_segment(value: str) -> str:
    if (not value or value.startswith('.') or any(c in value for c in '/\\\x00')
            or any(ord(c) < 32 for c in value) or value != value.strip()):
        raise ValueError(f"unsafe path segment: {value!r}")
    return value


def relative_path(value: str) -> str:
    if not value or value.startswith('/') or '\\' in value or '\x00' in value:
        raise ValueError(f"unsafe relative path: {value!r}")
    if any(p in {'', '.', '..'} or any(ord(c) < 32 for c in p) for p in value.split('/')):
        raise ValueError(f"unsafe relative path: {value!r}")
    return value


def node_path(value: str) -> str:
    for part in value.split('.'):
        safe_segment(part)
    if value.split('.')[0] != 'Main':
        raise ValueError('node path must start with Main')
    return value


Key = Annotated[str, AfterValidator(safe_segment)]
RelativePath = Annotated[str, AfterValidator(relative_path)]
NodePath = Annotated[str, AfterValidator(node_path)]
Text = Annotated[str, Field(min_length=1)]


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid')


class DeclRef(Model):
    repo: Key | None = None
    node: NodePath
    name: Key


class ExternalRef(Model):
    package: Text | None = None
    name: Text
    module: Text | None = None


class RepoDependency(Model):
    kind: Literal['repo_decl'] = 'repo_decl'
    ref: DeclRef
    reason: str | None = None


class ExternalDependency(Model):
    kind: Literal['external_decl'] = 'external_decl'
    ref: ExternalRef
    reason: str | None = None


Dependency = Annotated[RepoDependency | ExternalDependency, Field(discriminator='kind')]


class Origin(Model):
    kind: Text
    ref: str | None = None
    source_path: RelativePath | None = None
    resource_key: Key | None = None
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    start_locator: str | None = None
    end_locator: str | None = None
    note: str | None = None

    @model_validator(mode='after')
    def valid_location(self) -> Origin:
        if (self.start_line is None) != (self.end_line is None):
            raise ValueError('line range requires both endpoints')
        if self.start_line is not None and self.start_line > self.end_line:
            raise ValueError('reversed line range')
        if self.kind in {'source', 'resource'} and not self.source_path:
            raise ValueError('local origin requires a resolved source_path')
        return self


class NaturalLanguage(Model):
    text: str | None = None
    origin: list[Origin] = Field(default_factory=list)


class Section(Model):
    nl: NaturalLanguage | None = None
    fl: str | None = None
    deps: list[Dependency] = Field(default_factory=list)


class Declaration(Model):
    name: Key
    lean_name: Text
    kind: Text
    summary: str
    state: Literal['declared', 'proved']
    file: RelativePath
    statement: Section
    proof: Section | None = None

    @model_validator(mode='after')
    def complete_capture(self) -> Declaration:
        if not self.file.endswith('.lean'):
            raise ValueError('declaration file must be a Lean source file')
        if not self.statement.fl or not self.statement.fl.strip():
            raise ValueError('declaration requires statement.fl')
        if self.state == 'proved' and (self.proof is None or not self.proof.fl or not self.proof.fl.strip()):
            raise ValueError('proved declaration requires proof.fl')
        return self


class Node(Model):
    kind: Literal['scope', 'content']
    title: str | None = None
    goal: str
    boundary: str
    constraints: str | None = None
    summary: str | None = None
    exports: list[DeclRef] = Field(default_factory=list)
    children: list[NodePath] = Field(default_factory=list)
    declarations: list[Key] = Field(default_factory=list)

    @model_validator(mode='after')
    def ownership(self) -> Node:
        if self.kind == 'scope' and self.declarations:
            raise ValueError('scope cannot own declarations')
        if self.kind == 'content' and self.children:
            raise ValueError('content must be a leaf')
        if len(set(self.children)) != len(self.children) or len(set(self.declarations)) != len(self.declarations):
            raise ValueError('duplicate child or declaration')
        keys = [(r.repo, r.node, r.name) for r in self.exports]
        if len(set(keys)) != len(keys):
            raise ValueError('duplicate export')
        return self


class Repo(Model):
    root_node: Literal['Main'] = 'Main'
    module_root: Key


class Workspace(Model):
    title: str | None = None
    main_repo: Key
    repos: dict[Key, Key]

    @model_validator(mode='after')
    def main_exists(self) -> Workspace:
        if self.main_repo not in self.repos:
            raise ValueError('main_repo must be registered')
        if len(set(self.repos.values())) != len(self.repos):
            raise ValueError('repos must have distinct direct-child directories')
        return self


class RepoData(Model):
    """In-memory aggregate; node paths are keys, not duplicated stored fields."""
    metadata: Repo
    nodes: dict[NodePath, Node]
    declarations: dict[NodePath, dict[Key, Declaration]] = Field(default_factory=dict)


class WorkspaceData(Model):
    metadata: Workspace
    repos: dict[Key, RepoData]
