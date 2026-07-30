"""Provider-neutral scholarly resource discovery."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from typing import Any, Literal
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from pydantic import Field

from lean_constellation.domain.common import StrictModel


ResourceKind = Literal["paper", "book", "documentation", "web"]


class ExternalResourceDiscoveryConfig(StrictModel):
    openalex_base_url: str = "https://api.openalex.org"
    timeout_seconds: int = 20
    user_agent: str = "lean-constellation/1.0"
    max_summary_chars: int = 1200


class ExternalResourceCandidate(StrictModel):
    title: str
    resource_kind: ResourceKind
    canonical_locator: str
    authors: list[str] = Field(default_factory=list)
    version: str | None = None
    publication: str | None = None
    published_year: int | None = None
    summary: str | None = None
    identifiers: dict[str, str] = Field(default_factory=dict)
    source_urls: list[str] = Field(default_factory=list)


class ExternalResourceSearchResult(StrictModel):
    ok: bool
    query: str
    candidates: list[ExternalResourceCandidate] = Field(default_factory=list)
    summary: str
    issue_code: str | None = None


class ExternalResourceInspectResult(StrictModel):
    ok: bool
    target: str
    candidate: ExternalResourceCandidate | None = None
    summary: str
    issue_code: str | None = None


JsonTransport = Callable[[str, int, str], Mapping[str, Any]]


class ExternalResourceDiscoveryClient:
    """Bounded OpenAlex-backed discovery with a provider-neutral result contract."""

    def __init__(
        self,
        config: ExternalResourceDiscoveryConfig | None = None,
        *,
        transport: JsonTransport | None = None,
    ) -> None:
        self.config = config or ExternalResourceDiscoveryConfig()
        self._transport = transport or _request_json

    def search(
        self,
        query: str,
        *,
        kinds: list[ResourceKind] | None = None,
        limit: int = 10,
    ) -> ExternalResourceSearchResult:
        query = query.strip()
        if not query:
            raise ValueError("resource discovery query must be non-empty")
        if not 1 <= limit <= 20:
            raise ValueError("resource discovery limit must be between 1 and 20")
        requested_kinds = set(kinds or [])
        params = urlencode({"search": query, "per-page": limit})
        try:
            payload = self._transport(
                f"{self.config.openalex_base_url.rstrip('/')}/works?{params}",
                self.config.timeout_seconds,
                self.config.user_agent,
            )
            raw_results = payload.get("results", [])
            if not isinstance(raw_results, list):
                raise ValueError("OpenAlex response field results must be a list")
            candidates = [
                candidate
                for item in raw_results
                if isinstance(item, Mapping)
                and (candidate := self._candidate(item)) is not None
                and (not requested_kinds or candidate.resource_kind in requested_kinds)
            ][:limit]
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
            return ExternalResourceSearchResult(
                ok=False,
                query=query,
                summary=f"External resource discovery is temporarily unavailable: {exc}",
                issue_code="external_resource_discovery_unavailable",
            )
        return ExternalResourceSearchResult(
            ok=True,
            query=query,
            candidates=candidates,
            summary=f"Found {len(candidates)} bounded scholarly resource candidates.",
        )

    def inspect(self, target: str) -> ExternalResourceInspectResult:
        target = target.strip()
        if not target:
            raise ValueError("resource discovery target must be non-empty")
        openalex_id = _openalex_id(target)
        if openalex_id is not None:
            url = f"{self.config.openalex_base_url.rstrip('/')}/works/{quote(openalex_id, safe=':')}"
        else:
            doi = _doi(target)
            if doi is not None:
                url = f"{self.config.openalex_base_url.rstrip('/')}/works/https://doi.org/{quote(doi, safe='/')}"
            else:
                arxiv_id = _arxiv_id(target)
                if arxiv_id is None:
                    return ExternalResourceInspectResult(
                        ok=False,
                        target=target,
                        summary="Inspect requires an OpenAlex id, DOI, or arXiv locator.",
                        issue_code="external_resource_target_unsupported",
                    )
                params = urlencode({"search": arxiv_id, "per-page": 1})
                url = f"{self.config.openalex_base_url.rstrip('/')}/works?{params}"
        try:
            payload = self._transport(url, self.config.timeout_seconds, self.config.user_agent)
            if "results" in payload:
                results = payload.get("results", [])
                item = results[0] if isinstance(results, list) and results else None
            else:
                item = payload
            candidate = self._candidate(item) if isinstance(item, Mapping) else None
            if candidate is None:
                return ExternalResourceInspectResult(
                    ok=False,
                    target=target,
                    summary="No matching scholarly resource metadata was found.",
                    issue_code="external_resource_not_found",
                )
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
            return ExternalResourceInspectResult(
                ok=False,
                target=target,
                summary=f"External resource inspection is temporarily unavailable: {exc}",
                issue_code="external_resource_discovery_unavailable",
            )
        return ExternalResourceInspectResult(
            ok=True,
            target=target,
            candidate=candidate,
            summary=f"Inspected scholarly resource {candidate.title}.",
        )

    def _candidate(self, item: Mapping[str, Any]) -> ExternalResourceCandidate | None:
        title = str(item.get("display_name") or item.get("title") or "").strip()
        openalex = str(item.get("id") or "").strip()
        if not title or not openalex:
            return None
        item_type = str(item.get("type") or "").lower()
        kind: ResourceKind = "book" if item_type in {"book", "book-chapter"} else "paper"
        authorships = item.get("authorships", [])
        authors = [
            str(author.get("author", {}).get("display_name")).strip()
            for author in authorships
            if isinstance(author, Mapping)
            and isinstance(author.get("author"), Mapping)
            and author.get("author", {}).get("display_name")
        ][:20]
        ids = item.get("ids", {})
        if not isinstance(ids, Mapping):
            ids = {}
        identifiers = {
            str(key): str(value)
            for key, value in ids.items()
            if value
        }
        primary = item.get("primary_location", {})
        source = primary.get("source", {}) if isinstance(primary, Mapping) else {}
        publication = (
            str(source.get("display_name")).strip()
            if isinstance(source, Mapping) and source.get("display_name")
            else None
        )
        urls = _unique(
            [
                identifiers.get("doi"),
                identifiers.get("openalex"),
                str(primary.get("landing_page_url") or "") if isinstance(primary, Mapping) else None,
                str(primary.get("pdf_url") or "") if isinstance(primary, Mapping) else None,
            ]
        )
        summary = _reconstruct_abstract(item.get("abstract_inverted_index"))
        if summary and len(summary) > self.config.max_summary_chars:
            summary = summary[: self.config.max_summary_chars].rstrip() + "…"
        return ExternalResourceCandidate(
            title=title,
            resource_kind=kind,
            canonical_locator=identifiers.get("doi") or identifiers.get("openalex") or openalex,
            authors=_unique(authors),
            publication=publication,
            published_year=item.get("publication_year") if isinstance(item.get("publication_year"), int) else None,
            summary=summary,
            identifiers=identifiers,
            source_urls=urls,
        )


def _request_json(url: str, timeout_seconds: int, user_agent: str) -> Mapping[str, Any]:
    request = Request(url, headers={"Accept": "application/json", "User-Agent": user_agent})
    with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310 - fixed configured scholarly API.
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("external resource response must be a JSON object")
    return payload


def _reconstruct_abstract(value: object) -> str | None:
    if not isinstance(value, Mapping):
        return None
    positioned: list[tuple[int, str]] = []
    for word, positions in value.items():
        if not isinstance(word, str) or not isinstance(positions, list):
            continue
        positioned.extend((position, word) for position in positions if isinstance(position, int))
    return " ".join(word for _, word in sorted(positioned)) or None


def _doi(value: str) -> str | None:
    match = re.search(r"(?:doi\.org/|doi:)?(10\.\d{4,9}/\S+)", value, re.IGNORECASE)
    return match.group(1).rstrip(".,)") if match else None


def _arxiv_id(value: str) -> str | None:
    match = re.search(r"(?:arxiv:|arxiv\.org/(?:abs|pdf)/)?(\d{4}\.\d{4,5})(?:v\d+)?", value, re.IGNORECASE)
    return match.group(1) if match else None


def _openalex_id(value: str) -> str | None:
    match = re.search(r"(?:openalex\.org/)?(W\d+)$", value, re.IGNORECASE)
    return match.group(1).upper() if match else None


def _unique(values: list[str | None]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))
