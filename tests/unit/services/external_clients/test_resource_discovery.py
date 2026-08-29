from __future__ import annotations

from email.message import Message
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError

import pytest

from lean_constellation.services.external_clients.resource_discovery import (
    ExternalResourceDiscoveryClient,
    ExternalResourceDiscoveryConfig,
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


def _atom_payload(identity: str = "2606.24776v1") -> str:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/{identity}</id>
    <updated>2026-06-23T00:00:00Z</updated>
    <published>2026-06-23T00:00:00Z</published>
    <title>A disproof of the uniform witness conjecture</title>
    <summary>An exact arXiv abstract.</summary>
    <author><name>Zixiang Xu</name></author>
    <link href="https://arxiv.org/abs/{identity}" rel="alternate" type="text/html" />
    <link href="https://arxiv.org/pdf/{identity}" rel="related" type="application/pdf" />
  </entry>
</feed>
"""


def _http_error(
    code: int,
    *,
    headers: dict[str, str] | None = None,
    body: str = "",
) -> HTTPError:
    message = Message()
    for key, value in (headers or {}).items():
        message[key] = value
    return HTTPError(
        "https://api.openalex.org/works",
        code,
        "provider error",
        message,
        BytesIO(body.encode("utf-8")),
    )


def _pool(tmp_path: Path, text: str = "key-a\nkey-b\n") -> Path:
    path = tmp_path / "openalex-keys"
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)
    return path


def test_resource_discovery_search_returns_compact_canonical_candidates() -> None:
    seen: list[tuple[str, dict[str, str]]] = []

    def transport(url: str, timeout: int, user_agent: str, headers: dict[str, str]):
        seen.append((url, headers))
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
    assert "per-page=4" in seen[0][0]
    assert seen[0][1] == {}


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

    not_found = ExternalResourceDiscoveryClient(
        transport=lambda *_: (_ for _ in ()).throw(_http_error(404))
    ).inspect("W999")
    assert not not_found.ok
    assert not_found.issue_code == "external_resource_not_found"


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


def test_resource_discovery_key_pool_file_contract(tmp_path: Path) -> None:
    valid = _pool(tmp_path, "# preferred order\n key-a \n\nkey-b\nkey-a\n")
    seen: list[str] = []

    def transport(_url: str, _timeout: int, _user_agent: str, headers: dict[str, str]):
        seen.append(headers["Authorization"])
        return {"results": []}

    result = ExternalResourceDiscoveryClient(
        ExternalResourceDiscoveryConfig(openalex_api_keys_path=valid),
        transport=transport,
    ).search("finite combinatorics")
    assert result.ok
    assert seen == ["Bearer key-a"]

    missing = tmp_path / "missing"
    with pytest.raises(ValueError, match="unavailable"):
        ExternalResourceDiscoveryClient(
            ExternalResourceDiscoveryConfig(openalex_api_keys_path=missing)
        )

    valid.chmod(0o640)
    with pytest.raises(ValueError, match="group or other"):
        ExternalResourceDiscoveryClient(
            ExternalResourceDiscoveryConfig(openalex_api_keys_path=valid)
        )

    valid.write_text("key a\n", encoding="utf-8")
    valid.chmod(0o600)
    with pytest.raises(ValueError, match="whitespace"):
        ExternalResourceDiscoveryClient(
            ExternalResourceDiscoveryConfig(openalex_api_keys_path=valid)
        )

    valid.write_text("key-a,key-b\n", encoding="utf-8")
    with pytest.raises(ValueError, match="one key per line"):
        ExternalResourceDiscoveryClient(
            ExternalResourceDiscoveryConfig(openalex_api_keys_path=valid)
        )

    valid.write_text("# no credentials\n\n", encoding="utf-8")
    with pytest.raises(ValueError, match="at least one"):
        ExternalResourceDiscoveryClient(
            ExternalResourceDiscoveryConfig(openalex_api_keys_path=valid)
        )

    valid.write_bytes(b"\xff\xfe")
    with pytest.raises(ValueError, match="UTF-8"):
        ExternalResourceDiscoveryClient(
            ExternalResourceDiscoveryConfig(openalex_api_keys_path=valid)
        )


def test_resource_discovery_rotates_daily_exhausted_key_once(tmp_path: Path) -> None:
    pool = _pool(tmp_path)
    seen: list[str] = []

    def transport(_url: str, _timeout: int, _user_agent: str, headers: dict[str, str]):
        token = headers["Authorization"]
        seen.append(token)
        if token == "Bearer key-a":
            raise _http_error(
                429,
                headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "3600"},
                body='{"message":"Daily budget exhausted"}',
            )
        return {"results": [_work_payload()]}

    client = ExternalResourceDiscoveryClient(
        ExternalResourceDiscoveryConfig(openalex_api_keys_path=pool),
        transport=transport,
    )
    assert client.search("finite combinatorics").ok
    assert client.search("another query").ok
    assert seen == ["Bearer key-a", "Bearer key-b", "Bearer key-b"]


def test_resource_discovery_pool_exhaustion_is_typed_and_circuit_broken(tmp_path: Path) -> None:
    pool = _pool(tmp_path)
    calls = 0

    def transport(*_):
        nonlocal calls
        calls += 1
        raise _http_error(
            429,
            headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "3600"},
            body='{"message":"Daily credit budget exceeded"}',
        )

    client = ExternalResourceDiscoveryClient(
        ExternalResourceDiscoveryConfig(openalex_api_keys_path=pool),
        transport=transport,
    )
    first = client.search("finite combinatorics")
    second = client.search("finite combinatorics")

    assert not first.ok and first.issue_code == "external_resource_budget_exhausted"
    assert not second.ok and second.issue_code == "external_resource_budget_exhausted"
    assert calls == 2
    assert "key-a" not in first.summary and "key-b" not in first.summary


@pytest.mark.parametrize(
    ("code", "headers", "body", "expected"),
    [
        (429, {"Retry-After": "30"}, "rate limit exceeded", "external_resource_rate_limited"),
        (401, {}, "unauthorized", "external_resource_auth_unavailable"),
    ],
)
def test_resource_discovery_pool_terminal_errors_are_typed(
    tmp_path: Path,
    code: int,
    headers: dict[str, str],
    body: str,
    expected: str,
) -> None:
    pool = _pool(tmp_path)

    def transport(*_):
        raise _http_error(code, headers=headers, body=body)

    result = ExternalResourceDiscoveryClient(
        ExternalResourceDiscoveryConfig(openalex_api_keys_path=pool),
        transport=transport,
    ).search("finite combinatorics")

    assert not result.ok
    assert result.issue_code == expected


def test_resource_discovery_rotates_rejected_key_and_retains_successful_key(tmp_path: Path) -> None:
    pool = _pool(tmp_path)
    seen: list[str] = []

    def transport(_url: str, _timeout: int, _user_agent: str, headers: dict[str, str]):
        token = headers["Authorization"]
        seen.append(token)
        if token == "Bearer key-a":
            raise _http_error(401, body="unauthorized")
        return {"results": []}

    client = ExternalResourceDiscoveryClient(
        ExternalResourceDiscoveryConfig(openalex_api_keys_path=pool),
        transport=transport,
    )
    assert client.search("first").ok
    assert client.search("second").ok
    assert seen == ["Bearer key-a", "Bearer key-b", "Bearer key-b"]


def test_resource_discovery_generic_failure_does_not_disable_key(tmp_path: Path) -> None:
    pool = _pool(tmp_path, "key-a\n")
    calls = 0

    def transport(*_):
        nonlocal calls
        calls += 1
        raise TimeoutError("slow")

    client = ExternalResourceDiscoveryClient(
        ExternalResourceDiscoveryConfig(openalex_api_keys_path=pool),
        transport=transport,
    )
    assert client.search("first").issue_code == "external_resource_discovery_unavailable"
    assert client.search("second").issue_code == "external_resource_discovery_unavailable"
    assert calls == 2


def test_resource_discovery_arxiv_inspection_uses_exact_official_route_and_cache() -> None:
    openalex_called = False
    arxiv_urls: list[str] = []

    def openalex_transport(*_):
        nonlocal openalex_called
        openalex_called = True
        return {}

    def arxiv_transport(url: str, timeout: int, user_agent: str) -> str:
        arxiv_urls.append(url)
        assert timeout == 20
        assert user_agent
        return _atom_payload()

    client = ExternalResourceDiscoveryClient(
        transport=openalex_transport,
        arxiv_transport=arxiv_transport,
    )
    first = client.inspect("https://arxiv.org/abs/2606.24776v1")
    second = client.inspect("arxiv:2606.24776v1")

    assert first.ok and first.candidate is not None
    assert first.candidate.canonical_locator == "arxiv:2606.24776v1"
    assert first.candidate.version == "v1"
    assert first.candidate.authors == ["Zixiang Xu"]
    assert first.candidate.publication == "arXiv"
    assert first.candidate.published_year == 2026
    assert second.ok
    assert not openalex_called
    assert len(arxiv_urls) == 1
    assert "id_list=2606.24776v1" in arxiv_urls[0]


@pytest.mark.parametrize("target", ["arxiv:2606.24776", "arxiv:2606.24776v1"])
def test_resource_discovery_arxiv_inspection_rejects_wrong_atom_identifier(target: str) -> None:
    result = ExternalResourceDiscoveryClient(
        arxiv_transport=lambda *_: _atom_payload("2606.24777v1")
    ).inspect(target)

    assert not result.ok
    assert result.issue_code == "external_resource_not_found"
