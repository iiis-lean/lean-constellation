"""Readable selectors for current-schema material references."""

from __future__ import annotations

import re
from urllib.parse import quote, unquote_to_bytes

from lean_constellation.domain.refs import MaterialRef, ResourceRef, SourceRef


_RANGE_RE = re.compile(r"L([1-9][0-9]*)-L([1-9][0-9]*)")
_INVALID_ESCAPE_RE = re.compile(r"%(?![0-9A-Fa-f]{2})")


def format_material_ref(ref: MaterialRef) -> str:
    """Format one structured material ref as a stable, copyable selector."""

    start_line, end_line = _range_for_ref(ref)
    suffix = "" if start_line is None else f"#L{start_line}-L{end_line}"
    if ref.kind == "source" and isinstance(ref.ref, SourceRef):
        path = _encode(ref.ref.path, safe="/")
        if not path:
            raise ValueError("source material ref path must be non-empty")
        return f"source:{path}{suffix}"
    if ref.kind == "resource" and isinstance(ref.ref, ResourceRef):
        resource_key = _encode(ref.ref.resource_key, safe="")
        if not resource_key:
            raise ValueError("resource material ref key must be non-empty")
        locator = ""
        if ref.ref.locator is not None:
            encoded_locator = _encode(ref.ref.locator, safe="/")
            if not encoded_locator:
                raise ValueError("resource material ref locator must be non-empty when present")
            locator = f"/{encoded_locator}"
        return f"resource:{resource_key}{locator}{suffix}"
    raise ValueError("material ref kind does not match its structured value")


def parse_material_ref(selector: str) -> MaterialRef:
    """Parse a system material selector back into current-schema truth fields."""

    if not isinstance(selector, str) or not selector.strip() or selector != selector.strip():
        raise ValueError("material ref selector must be a non-empty trimmed string")
    base, start_line, end_line = _split_range(selector)
    if base.startswith("source:"):
        path = _decode(base.removeprefix("source:"))
        if not path:
            raise ValueError("source material ref path must be non-empty")
        return MaterialRef(
            kind="source",
            ref=SourceRef(path=path, start_line=start_line, end_line=end_line),
        )
    if base.startswith("resource:"):
        payload = base.removeprefix("resource:")
        encoded_key, separator, encoded_locator = payload.partition("/")
        resource_key = _decode(encoded_key)
        if not resource_key:
            raise ValueError("resource material ref key must be non-empty")
        locator = None
        if separator:
            locator = _decode(encoded_locator)
            if not locator:
                raise ValueError("resource material ref locator must be non-empty when present")
        return MaterialRef(
            kind="resource",
            ref=ResourceRef(
                resource_key=resource_key,
                locator=locator,
                start_line=start_line,
                end_line=end_line,
            ),
        )
    raise ValueError("material ref selector must start with source: or resource:")


def _range_for_ref(ref: MaterialRef) -> tuple[int | None, int | None]:
    start_line = ref.ref.start_line
    end_line = ref.ref.end_line
    if (start_line is None) != (end_line is None):
        raise ValueError("material ref start_line and end_line must be present together")
    if start_line is not None and end_line is not None and not (1 <= start_line <= end_line):
        raise ValueError("material ref line range is invalid")
    return start_line, end_line


def _split_range(selector: str) -> tuple[str, int | None, int | None]:
    base, marker, encoded_range = selector.rpartition("#")
    if not marker:
        return selector, None, None
    match = _RANGE_RE.fullmatch(encoded_range)
    if match is None:
        raise ValueError("material ref line range must use #L<start>-L<end>")
    start_line = int(match.group(1))
    end_line = int(match.group(2))
    if start_line > end_line:
        raise ValueError("material ref line range is invalid")
    return base, start_line, end_line


def _encode(value: str, *, safe: str) -> str:
    return quote(value, safe=safe, encoding="utf-8", errors="strict")


def _decode(value: str) -> str:
    if _INVALID_ESCAPE_RE.search(value):
        raise ValueError("material ref selector contains an invalid percent escape")
    try:
        return unquote_to_bytes(value).decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValueError("material ref selector is not valid UTF-8") from exc
