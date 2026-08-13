from __future__ import annotations

from lean_constellation.services.tool_facade import SubmitBehavior
from tests.unit.tools._submit_family_helpers import assert_submit_tools


def test_native_preparation_submit_tools_registered() -> None:
    assert_submit_tools(
        {
            "submit_source_corpus_builder_ready",
            "submit_source_corpus_builder_blocked",
            "submit_source_corpus_review",
            "submit_source_index_builder_round",
            "submit_source_index_review_round",
            "submit_root_interface_prepare_ready",
        },
        behavior=SubmitBehavior.TERMINAL,
    )
