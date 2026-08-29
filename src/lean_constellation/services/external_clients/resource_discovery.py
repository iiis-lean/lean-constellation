"""Provider-neutral scholarly resource discovery."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import stat
import time
from typing import Any, Literal
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

from pydantic import Field, field_validator

from lean_constellation.domain.common import StrictModel


ResourceKind = Literal["paper", "book"]


class ExternalResourceDiscoveryConfig(StrictModel):
    openalex_base_url: str = "https://api.openalex.org"
    arxiv_api_base_url: str = "https://export.arxiv.org/api/query"
    openalex_api_keys_path: Path | None = None
    timeout_seconds: int = 20
    user_agent: str = "lean-constellation/1.0"
    max_summary_chars: int = 1200

    @field_validator("openalex_api_keys_path", mode="before")
    @classmethod
    def _coerce_key_pool_path(cls, value: object) -> Path | None:
        if value is None or isinstance(value, Path):
            return value
        return Path(str(value)).expanduser()


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


JsonTransport = Callable[[str, int, str, Mapping[str, str]], Mapping[str, Any]]
TextTransport = Callable[[str, int, str], str]
Clock = Callable[[], float]


@dataclass
class _CredentialState:
    token: str | None = field(repr=False)
    unavailable_until: float = 0.0
    reason: Literal["ready", "rate", "budget", "auth"] = "ready"


class _ProviderRequestError(Exception):
    def __init__(self, issue_code: str, summary: str) -> None:
        super().__init__(summary)
        self.issue_code = issue_code
        self.summary = summary


class ExternalResourceDiscoveryClient:
    """Bounded scholarly discovery with provider-specific routing behind one facade."""

    _INSPECT_CACHE_MAX_ENTRIES = 128

    def __init__(
        self,
        config: ExternalResourceDiscoveryConfig | None = None,
        *,
        transport: JsonTransport | None = None,
        arxiv_transport: TextTransport | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.config = config or ExternalResourceDiscoveryConfig()
        self._transport = transport or _request_json
        self._arxiv_transport = arxiv_transport or _request_text
        self._clock = clock or time.monotonic
        tokens = _load_key_pool(self.config.openalex_api_keys_path)
        self._credentials = [_CredentialState(token=token) for token in tokens]
        self._anonymous = _CredentialState(token=None)
        self._active_credential_index = 0
        self._inspect_cache: OrderedDict[str, ExternalResourceInspectResult] = OrderedDict()

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
            payload = self._request_openalex(
                f"{self.config.openalex_base_url.rstrip('/')}/works?{params}"
            )
            raw_results = payload.get("results", [])
            if not isinstance(raw_results, list):
                raise ValueError("OpenAlex response field results must be a list")
            candidates = [
                candidate
                for item in raw_results
                if isinstance(item, Mapping)
                and (candidate := self._openalex_candidate(item)) is not None
                and (not requested_kinds or candidate.resource_kind in requested_kinds)
            ][:limit]
        except _ProviderRequestError as exc:
            return ExternalResourceSearchResult(
                ok=False,
                query=query,
                summary=exc.summary,
                issue_code=exc.issue_code,
            )
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
        resolved = self._inspect_target(target)
        if resolved is None:
            return ExternalResourceInspectResult(
                ok=False,
                target=target,
                summary="Inspect requires an OpenAlex id, DOI, or arXiv locator.",
                issue_code="external_resource_target_unsupported",
            )
        provider, identity = resolved
        cache_key = f"{provider}:{identity.casefold()}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached.model_copy(update={"target": target}, deep=True)
        try:
            if provider == "arxiv":
                candidate = self._inspect_arxiv(identity)
            else:
                candidate = self._inspect_openalex(provider, identity)
        except _ProviderRequestError as exc:
            return ExternalResourceInspectResult(
                ok=False,
                target=target,
                summary=exc.summary,
                issue_code=exc.issue_code,
            )
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError, ET.ParseError) as exc:
            return ExternalResourceInspectResult(
                ok=False,
                target=target,
                summary=f"External resource inspection is temporarily unavailable: {exc}",
                issue_code="external_resource_discovery_unavailable",
            )
        if candidate is None:
            return ExternalResourceInspectResult(
                ok=False,
                target=target,
                summary="No matching scholarly resource metadata was found.",
                issue_code="external_resource_not_found",
            )
        result = ExternalResourceInspectResult(
            ok=True,
            target=target,
            candidate=candidate,
            summary=f"Inspected scholarly resource {candidate.title}.",
        )
        self._cache_put(cache_key, result)
        return result

    def _inspect_target(self, target: str) -> tuple[str, str] | None:
        openalex_id = _openalex_id(target)
        if openalex_id is not None:
            return "openalex", openalex_id
        doi = _doi(target)
        if doi is not None:
            return "doi", doi
        arxiv = _arxiv_locator(target)
        if arxiv is not None:
            identifier, version = arxiv
            return "arxiv", f"{identifier}{version or ''}"
        return None

    def _inspect_openalex(self, provider: str, identity: str) -> ExternalResourceCandidate | None:
        if provider == "openalex":
            url = f"{self.config.openalex_base_url.rstrip('/')}/works/{quote(identity, safe=':')}"
        else:
            url = (
                f"{self.config.openalex_base_url.rstrip('/')}/works/"
                f"https://doi.org/{quote(identity, safe='/')}"
            )
        payload = self._request_openalex(url)
        return self._openalex_candidate(payload)

    def _inspect_arxiv(self, identity: str) -> ExternalResourceCandidate | None:
        params = urlencode({"id_list": identity})
        payload = self._arxiv_transport(
            f"{self.config.arxiv_api_base_url}?{params}",
            self.config.timeout_seconds,
            self.config.user_agent,
        )
        return self._arxiv_candidate(payload, requested_identity=identity)

    def _request_openalex(self, url: str) -> Mapping[str, Any]:
        now = self._clock()
        states = self._credentials or [self._anonymous]
        if self._credentials:
            order = [
                (self._active_credential_index + offset) % len(states)
                for offset in range(len(states))
            ]
        else:
            order = [0]
        attempted = False
        for index in order:
            state = states[index]
            if state.unavailable_until > now:
                continue
            if state.reason != "ready":
                state.reason = "ready"
            attempted = True
            headers = {"Authorization": f"Bearer {state.token}"} if state.token else {}
            try:
                payload = self._transport(
                    url,
                    self.config.timeout_seconds,
                    self.config.user_agent,
                    headers,
                )
            except HTTPError as exc:
                failure = _classify_openalex_http_error(exc)
                if failure.reason in {"rate", "budget", "auth"}:
                    state.reason = failure.reason
                    state.unavailable_until = (
                        float("inf")
                        if failure.reason == "auth"
                        else now + failure.cooldown_seconds
                    )
                    continue
                raise _ProviderRequestError(failure.issue_code, failure.summary) from exc
            if self._credentials:
                self._active_credential_index = index
            return payload
        raise self._pool_unavailable_error(states, now, attempted=attempted)

    def _pool_unavailable_error(
        self,
        states: list[_CredentialState],
        now: float,
        *,
        attempted: bool,
    ) -> _ProviderRequestError:
        reasons = {state.reason for state in states if state.reason != "ready"}
        finite_waits = [
            max(0, int(state.unavailable_until - now))
            for state in states
            if state.unavailable_until != float("inf") and state.unavailable_until > now
        ]
        wait_summary = (
            f" Earliest retry is in about {min(finite_waits)} seconds."
            if finite_waits
            else ""
        )
        if "rate" in reasons:
            code = "external_resource_rate_limited"
            reason = "OpenAlex credentials are temporarily rate limited."
        elif "budget" in reasons:
            code = "external_resource_budget_exhausted"
            reason = "The available OpenAlex daily budget is exhausted."
        elif "auth" in reasons:
            code = "external_resource_auth_unavailable"
            reason = "No configured OpenAlex credential is currently accepted."
        else:
            code = "external_resource_discovery_unavailable"
            reason = "OpenAlex resource discovery is unavailable."
        suffix = " The configured pool was tried once." if attempted else ""
        return _ProviderRequestError(code, f"{reason}{wait_summary}{suffix}")

    def _openalex_candidate(self, item: Mapping[str, Any]) -> ExternalResourceCandidate | None:
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
        identifiers = {str(key): str(value) for key, value in ids.items() if value}
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
        summary = _bounded_summary(
            _reconstruct_abstract(item.get("abstract_inverted_index")),
            self.config.max_summary_chars,
        )
        return ExternalResourceCandidate(
            title=title,
            resource_kind=kind,
            canonical_locator=identifiers.get("doi") or identifiers.get("openalex") or openalex,
            authors=_unique(authors),
            publication=publication,
            published_year=(
                item.get("publication_year")
                if isinstance(item.get("publication_year"), int)
                else None
            ),
            summary=summary,
            identifiers=identifiers,
            source_urls=urls,
        )

    def _arxiv_candidate(
        self,
        payload: str,
        *,
        requested_identity: str,
    ) -> ExternalResourceCandidate | None:
        root = ET.fromstring(payload)
        namespace = {"atom": "http://www.w3.org/2005/Atom"}
        entry = root.find("atom:entry", namespace)
        if entry is None:
            return None
        title = _element_text(entry, "atom:title", namespace)
        entry_id = _element_text(entry, "atom:id", namespace)
        if not title or not entry_id:
            return None
        parsed = _arxiv_locator(entry_id)
        requested = _arxiv_locator(f"arxiv:{requested_identity}")
        if parsed is None or requested is None:
            return None
        identifier, version = parsed
        if identifier.casefold() != requested[0].casefold():
            return None
        if requested[1] is not None and version != requested[1]:
            return None
        identity = f"{identifier}{version or ''}"
        abs_url = f"https://arxiv.org/abs/{identity}"
        pdf_url = f"https://arxiv.org/pdf/{identity}"
        links = [
            str(link.get("href")).strip()
            for link in entry.findall("atom:link", namespace)
            if link.get("href")
        ]
        authors = [
            name
            for author in entry.findall("atom:author", namespace)
            if (name := _element_text(author, "atom:name", namespace))
        ]
        published = _element_text(entry, "atom:published", namespace)
        year = int(published[:4]) if published and re.match(r"^\d{4}", published) else None
        summary = _bounded_summary(
            _element_text(entry, "atom:summary", namespace),
            self.config.max_summary_chars,
        )
        return ExternalResourceCandidate(
            title=_normalize_space(title),
            resource_kind="paper",
            canonical_locator=f"arxiv:{identity}",
            authors=_unique(authors)[:20],
            version=version,
            publication="arXiv",
            published_year=year,
            summary=summary,
            identifiers={"arxiv": abs_url},
            source_urls=_unique([abs_url, pdf_url, *links]),
        )

    def _cache_get(self, key: str) -> ExternalResourceInspectResult | None:
        cached = self._inspect_cache.get(key)
        if cached is None:
            return None
        self._inspect_cache.move_to_end(key)
        return cached.model_copy(deep=True)

    def _cache_put(self, key: str, value: ExternalResourceInspectResult) -> None:
        self._inspect_cache[key] = value.model_copy(deep=True)
        self._inspect_cache.move_to_end(key)
        while len(self._inspect_cache) > self._INSPECT_CACHE_MAX_ENTRIES:
            self._inspect_cache.popitem(last=False)


@dataclass(frozen=True)
class _HttpFailure:
    issue_code: str
    summary: str
    reason: Literal["rate", "budget", "auth", "other"]
    cooldown_seconds: float = 0.0


def _classify_openalex_http_error(exc: HTTPError) -> _HttpFailure:
    headers = {str(key).casefold(): str(value) for key, value in (exc.headers or {}).items()}
    try:
        body = exc.read(4096).decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001 - error detail is best-effort and non-authoritative.
        body = ""
    body_lower = body.casefold()
    if exc.code in {401, 403}:
        return _HttpFailure(
            issue_code="external_resource_auth_unavailable",
            summary="An OpenAlex credential was rejected.",
            reason="auth",
        )
    if exc.code == 404:
        return _HttpFailure(
            issue_code="external_resource_not_found",
            summary="No matching scholarly resource metadata was found.",
            reason="other",
        )
    if exc.code == 429:
        remaining = headers.get("x-ratelimit-remaining")
        daily = remaining == "0" or any(
            marker in body_lower for marker in ("daily", "budget", "credit")
        )
        reset = _positive_seconds(headers.get("x-ratelimit-reset"))
        retry_after = _positive_seconds(headers.get("retry-after"))
        cooldown = reset if daily and reset is not None else retry_after or reset or 60.0
        if daily:
            return _HttpFailure(
                issue_code="external_resource_budget_exhausted",
                summary="An OpenAlex credential has exhausted its daily budget.",
                reason="budget",
                cooldown_seconds=cooldown,
            )
        return _HttpFailure(
            issue_code="external_resource_rate_limited",
            summary="An OpenAlex credential is temporarily rate limited.",
            reason="rate",
            cooldown_seconds=cooldown,
        )
    return _HttpFailure(
        issue_code="external_resource_discovery_unavailable",
        summary=f"OpenAlex request failed with HTTP {exc.code}.",
        reason="other",
    )


def _load_key_pool(path: Path | None) -> list[str]:
    if path is None:
        return []
    resolved = path.expanduser()
    try:
        file_stat = resolved.lstat()
    except OSError as exc:
        raise ValueError(f"OpenAlex key-pool file is unavailable: {resolved}") from exc
    if not stat.S_ISREG(file_stat.st_mode):
        raise ValueError("OpenAlex key-pool path must be a regular file")
    mode = stat.S_IMODE(file_stat.st_mode)
    if mode & 0o077:
        raise ValueError("OpenAlex key-pool file must not grant group or other permissions")
    if not mode & stat.S_IRUSR:
        raise ValueError("OpenAlex key-pool file must be readable by its owner")
    try:
        text = resolved.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValueError("OpenAlex key-pool file must be readable UTF-8 text") from exc
    keys: list[str] = []
    seen: set[str] = set()
    for raw_line in text.splitlines():
        candidate = raw_line.strip()
        if not candidate or candidate.startswith("#"):
            continue
        if "," in candidate:
            raise ValueError("OpenAlex key-pool entries must use one key per line")
        if any(character.isspace() for character in candidate):
            raise ValueError("OpenAlex key-pool entries must not contain whitespace")
        if candidate not in seen:
            seen.add(candidate)
            keys.append(candidate)
    if not keys:
        raise ValueError("OpenAlex key-pool file must contain at least one key")
    return keys


def _request_json(
    url: str,
    timeout_seconds: int,
    user_agent: str,
    extra_headers: Mapping[str, str],
) -> Mapping[str, Any]:
    headers = {"Accept": "application/json", "User-Agent": user_agent, **extra_headers}
    request = Request(url, headers=headers)
    with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310 - configured scholarly API.
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("external resource response must be a JSON object")
    return payload


def _request_text(url: str, timeout_seconds: int, user_agent: str) -> str:
    request = Request(
        url,
        headers={"Accept": "application/atom+xml", "User-Agent": user_agent},
    )
    with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310 - fixed arXiv API.
        return response.read().decode("utf-8")


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


def _arxiv_locator(value: str) -> tuple[str, str | None] | None:
    candidate = value.strip()
    if candidate[:6].casefold() == "arxiv:":
        candidate = candidate[6:]
    else:
        match = re.search(r"arxiv\.org/(?:abs|pdf)/([^?#]+)", candidate, re.IGNORECASE)
        if match is None:
            if re.fullmatch(r"(?:[a-z-]+(?:\.[A-Z]{2})?/\d{7}|\d{4}\.\d{4,5})(?:v\d+)?", candidate, re.IGNORECASE) is None:
                return None
        else:
            candidate = match.group(1)
            if candidate.casefold().endswith(".pdf"):
                candidate = candidate[:-4]
    match = re.fullmatch(
        r"(?P<identifier>[a-z-]+(?:\.[A-Z]{2})?/\d{7}|\d{4}\.\d{4,5})(?P<version>v\d+)?",
        candidate,
        re.IGNORECASE,
    )
    if match is None:
        return None
    identifier = match.group("identifier")
    if "/" in identifier:
        identifier = identifier.casefold()
    version = match.group("version")
    return identifier, version.casefold() if version else None


def _openalex_id(value: str) -> str | None:
    match = re.search(r"(?:openalex\.org/)?(W\d+)$", value, re.IGNORECASE)
    return match.group(1).upper() if match else None


def _element_text(element: ET.Element, path: str, namespace: Mapping[str, str]) -> str | None:
    child = element.find(path, namespace)
    if child is None or child.text is None:
        return None
    return _normalize_space(child.text) or None


def _normalize_space(value: str) -> str:
    return " ".join(value.split())


def _bounded_summary(value: str | None, limit: int) -> str | None:
    if not value:
        return None
    normalized = _normalize_space(value)
    if len(normalized) <= limit:
        return normalized
    return normalized[:limit].rstrip() + "…"


def _positive_seconds(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def _unique(values: list[str | None]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))
