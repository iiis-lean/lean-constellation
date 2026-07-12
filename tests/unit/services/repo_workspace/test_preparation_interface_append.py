from __future__ import annotations

from pathlib import Path

from lean_constellation.domain.interface import DeclInterface, DeclKind
from lean_constellation.domain.preparation import RepoPreparationInput, RepoRequirementRef, SourceCorpusMode
from tests.unit_services_helpers import make_runtime


def _input() -> RepoPreparationInput:
    return RepoPreparationInput(
        goal="Preserve the long-term repository goal.",
        source_corpus_mode=SourceCorpusMode.EXISTING,
        source_corpus_relpath=".lean_constellation/source",
        source_description="A stable source description.",
        interface_inputs=[
            DeclInterface(
                name="Existing.result",
                kind=DeclKind.THEOREM,
                summary="The existing required result.",
                expected_statement_lean_code="theorem Existing.result : True",
            )
        ],
        allow_interface_supplement=False,
        requirement_refs=[RepoRequirementRef(consumer_repo="Consumer", requirement_name="need_result")],
        notes="Keep these notes unchanged.",
    )


def test_preparation_interface_append_preserves_existing_input_fields(tmp_path: Path) -> None:
    runtime = make_runtime()
    original = _input()
    assert runtime.repo_workspace.write_preparation_input(tmp_path, input=original).ok
    added = DeclInterface(
        name="New.definition",
        kind=DeclKind.DEFINITION,
        summary="A newly required public definition.",
    )

    preview = runtime.repo_workspace.preview_preparation_interface_append(tmp_path, interfaces=[added])
    assert preview.ok and preview.value is not None
    assert preview.value.added_names == ["New.definition"]
    assert preview.value.changed is True

    appended = runtime.repo_workspace.append_preparation_interfaces(tmp_path, interfaces=[added])
    assert appended.ok and appended.value is not None
    assert appended.value.added_names == ["New.definition"]
    assert appended.value.total_count == 2
    loaded = runtime.repo_workspace.preparation.get_preparation_input(tmp_path)
    assert loaded.ok and loaded.value is not None
    expected = original.model_copy(deep=True)
    expected.interface_inputs.append(added)
    assert loaded.value.input.model_dump(mode="json") == expected.model_dump(mode="json")


def test_preparation_interface_append_same_payload_is_idempotent_without_write(tmp_path: Path) -> None:
    runtime = make_runtime()
    original = _input()
    assert runtime.repo_workspace.write_preparation_input(tmp_path, input=original).ok
    path = tmp_path / ".lean_constellation" / "preparation_input.json"
    before = path.read_bytes()

    appended = runtime.repo_workspace.append_preparation_interfaces(
        tmp_path,
        interfaces=[original.interface_inputs[0].model_copy(deep=True)],
    )

    assert appended.ok and appended.value is not None
    assert appended.value.changed is False
    assert appended.value.existing_names == ["Existing.result"]
    assert path.read_bytes() == before


def test_preparation_interface_append_conflict_rolls_back_whole_batch(tmp_path: Path) -> None:
    runtime = make_runtime()
    original = _input()
    assert runtime.repo_workspace.write_preparation_input(tmp_path, input=original).ok
    path = tmp_path / ".lean_constellation" / "preparation_input.json"
    before = path.read_bytes()
    new_interface = DeclInterface(
        name="WouldBeAdded",
        kind=DeclKind.LEMMA,
        summary="This must not be partially appended.",
    )
    conflict = original.interface_inputs[0].model_copy(update={"summary": "Changed meaning."})

    appended = runtime.repo_workspace.append_preparation_interfaces(
        tmp_path,
        interfaces=[new_interface, conflict],
    )

    assert not appended.ok
    assert appended.issues[0].kind == "protected_interface_conflict"
    assert path.read_bytes() == before


def test_preparation_interface_append_rejects_duplicate_request_names(tmp_path: Path) -> None:
    runtime = make_runtime()
    assert runtime.repo_workspace.write_preparation_input(tmp_path, input=_input()).ok
    first = DeclInterface(name="Duplicate", kind=DeclKind.THEOREM, summary="First.")
    second = first.model_copy(deep=True)

    appended = runtime.repo_workspace.append_preparation_interfaces(
        tmp_path,
        interfaces=[first, second],
    )

    assert not appended.ok
    assert appended.issues[0].kind == "interface_duplicate"
