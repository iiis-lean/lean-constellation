from __future__ import annotations

from lean_constellation.services.external_clients.resource_discovery import (
    ExternalResourceDiscoveryClient,
)


def _work_payload():
    return {
        "id": "https://openalex.org/W123",
        "display_name": "A finite combinatorics result",
        "type": "article",
        "publication_year": 2026,
        "authorships": [
            {"author": {"display_name": "Ada Example"}},
        ],
        "ids": {
            "openalex": "https://openalex.org/W123",
            "doi": "https://doi.org/10.1000/example",
        },
        "primary_location": {
            "landing_page_url": "https://doi.org/10.1000/example",
            "source": {"display_name": "Example Journal"},
        },
        "abstract_inverted_index": {
            "Finite": [0],
            "result": [1],
        },
    }


def test_resource_discovery_search_returns_compact_canonical_candidates() -> None:
    seen: list[str] = []

    def transport(url: str, timeout: int, user_agent: str):
        seen.append(url)
        assert timeout == 20
        assert user_agent
        return {"results": [_work_payload()]}

    result = ExternalResourceDiscoveryClient(transport=transport).search(
        "finite combinatorics",
        kinds=["paper"],
        limit=4,
    )

    assert result.ok
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.canonical_locator == "https://doi.org/10.1000/example"
    assert candidate.authors == ["Ada Example"]
    assert candidate.summary == "Finite result"
    assert "per-page=4" in seen[0]


def test_resource_discovery_inspect_accepts_doi_and_reports_unavailable() -> None:
    inspected = ExternalResourceDiscoveryClient(
        transport=lambda *_: _work_payload()
    ).inspect("doi:10.1000/example")
    assert inspected.ok
    assert inspected.candidate is not None
    assert inspected.candidate.title == "A finite combinatorics result"

    unavailable = ExternalResourceDiscoveryClient(
        transport=lambda *_: (_ for _ in ()).throw(TimeoutError("slow"))
    ).search("finite combinatorics")
    assert not unavailable.ok
    assert unavailable.issue_code == "external_resource_discovery_unavailable"


def test_resource_discovery_rejects_unsupported_inspect_target_without_network() -> None:
    called = False

    def transport(*_):
        nonlocal called
        called = True
        return {}

    result = ExternalResourceDiscoveryClient(transport=transport).inspect(
        "some unqualified title"
    )

    assert not result.ok
    assert result.issue_code == "external_resource_target_unsupported"
    assert not called
