"""Deterministic Graphviz layout for publication declaration graphs."""

from __future__ import annotations

from dataclasses import dataclass
import html
import json
import math
import re
import shutil
import subprocess
from typing import Literal, Protocol


DeclarationKey = tuple[str, str]
DependencyKind = Literal["Statement", "Proof"]


class PublicationDeclarationLike(Protocol):
    name: str
    node_path: str
    kind: str
    state: str
    proof_available: bool


class PublicationTreeNodeLike(Protocol):
    path: str
    parent_path: str | None


class PublicationTreeLike(Protocol):
    nodes: list[PublicationTreeNodeLike]


@dataclass(frozen=True)
class _LayoutCandidate:
    svg: str
    direction: Literal["TB", "LR"]
    spacing: Literal["standard", "compact", "balanced"]
    routing: Literal["spline"]
    hierarchy: Literal["nested", "content"]
    wrap_chars: int
    width: float
    height: float
    score: float


class PublicationGraphvizUnavailable(RuntimeError):
    """Raised when the optional Graphviz publication renderer is unavailable."""


def render_publication_graph_svg(
    *,
    tree: PublicationTreeLike,
    declarations: list[PublicationDeclarationLike],
    propagation: dict[DeclarationKey, list[str]],
    dependency_edges: list[tuple[DeclarationKey, DeclarationKey, DependencyKind]],
    declaration_links: dict[DeclarationKey, str],
    title: str,
) -> str:
    """Render the most compact readable Graphviz candidate as static SVG."""

    dot = shutil.which("dot")
    if dot is None:
        raise PublicationGraphvizUnavailable(
            "Graphviz 'dot' is required for publication graph rendering."
        )
    if not declarations:
        return _render_empty_svg(title)

    candidates: list[_LayoutCandidate] = []
    configurations: tuple[
        tuple[
            Literal["TB", "LR"],
            Literal["standard", "compact", "balanced"],
        ],
        ...,
    ] = (
        ("LR", "compact"),
        ("LR", "balanced"),
    )
    for hierarchy in ("nested",):
        for direction, spacing in configurations:
            for routing in ("spline",):
                for wrap_chars in (64, 80):
                    source = _render_dot_source(
                        tree=tree,
                        declarations=declarations,
                        propagation=propagation,
                        dependency_edges=dependency_edges,
                        declaration_links=declaration_links,
                        direction=direction,
                        spacing=spacing,
                        routing=routing,
                        hierarchy=hierarchy,
                        wrap_chars=wrap_chars,
                        vertical_order=None,
                    )
                    try:
                        rendered = subprocess.run(
                            [dot, "-Tsvg"],
                            input=source,
                            text=True,
                            capture_output=True,
                            check=False,
                            timeout=30,
                        )
                    except subprocess.SubprocessError:
                        continue
                    if rendered.returncode != 0:
                        continue
                    vertical_order = _svg_declaration_vertical_order(
                        rendered.stdout,
                        declaration_ids={
                            (declaration.node_path, declaration.name): (
                                "decl-"
                                + _safe_identifier(
                                    declaration.node_path,
                                    declaration.name,
                                )
                            )
                            for declaration in declarations
                        },
                    )
                    if vertical_order:
                        refined_source = _render_dot_source(
                            tree=tree,
                            declarations=declarations,
                            propagation=propagation,
                            dependency_edges=dependency_edges,
                            declaration_links=declaration_links,
                            direction=direction,
                            spacing=spacing,
                            routing=routing,
                            hierarchy=hierarchy,
                            wrap_chars=wrap_chars,
                            vertical_order=vertical_order,
                        )
                        try:
                            refined = subprocess.run(
                                [dot, "-Tsvg"],
                                input=refined_source,
                                text=True,
                                capture_output=True,
                                check=False,
                                timeout=30,
                            )
                        except subprocess.SubprocessError:
                            refined = None
                        if refined is not None and refined.returncode == 0:
                            rendered = refined
                    dimensions = _svg_dimensions(rendered.stdout)
                    if dimensions is None:
                        continue
                    width, height = dimensions
                    candidates.append(
                        _LayoutCandidate(
                            svg=rendered.stdout,
                            direction=direction,
                            spacing=spacing,
                            routing=routing,
                            hierarchy=hierarchy,
                            wrap_chars=wrap_chars,
                            width=width,
                            height=height,
                            score=_candidate_score(
                                svg=rendered.stdout,
                                width=width,
                                height=height,
                                declaration_count=len(declarations),
                                direction=direction,
                                routing=routing,
                                hierarchy=hierarchy,
                                wrap_chars=wrap_chars,
                            ),
                        )
                    )
    if not candidates:
        raise PublicationGraphvizUnavailable(
            "Graphviz did not produce a valid publication graph candidate."
        )
    selected = min(
        candidates,
        key=lambda item: (
            item.score,
            item.hierarchy != "nested",
            item.direction != "TB",
            item.wrap_chars,
        ),
    )
    metadata = (
        "<!-- Lean Constellation layout: "
        f"{selected.direction.lower()}-{selected.spacing}-{selected.routing}-"
        f"{selected.hierarchy}; "
        f"wrap={selected.wrap_chars}; ratio={selected.width / selected.height:.3f} "
        "-->\n"
    )
    return metadata + _normalize_graphviz_svg(selected.svg, title=title)


def _render_dot_source(
    *,
    tree: PublicationTreeLike,
    declarations: list[PublicationDeclarationLike],
    propagation: dict[DeclarationKey, list[str]],
    dependency_edges: list[tuple[DeclarationKey, DeclarationKey, DependencyKind]],
    declaration_links: dict[DeclarationKey, str],
    direction: Literal["TB", "LR"],
    spacing: Literal["standard", "compact", "balanced"],
    routing: Literal["spline"],
    hierarchy: Literal["nested", "content"],
    wrap_chars: int,
    vertical_order: dict[DeclarationKey, float] | None,
) -> str:
    ordered = sorted(
        declarations,
        key=lambda item: (item.node_path, item.name),
    )
    by_content: dict[str, list[PublicationDeclarationLike]] = {}
    for declaration in ordered:
        by_content.setdefault(declaration.node_path, []).append(declaration)
    declaration_ids = {
        (declaration.node_path, declaration.name): f"decl_{index}"
        for index, declaration in enumerate(ordered)
    }
    tree_nodes = {node.path: node for node in tree.nodes}
    relevant = set(by_content)
    for content_path in by_content:
        parent = getattr(tree_nodes.get(content_path), "parent_path", None)
        while parent is not None:
            relevant.add(parent)
            parent = getattr(tree_nodes.get(parent), "parent_path", None)
    if "Main" in tree_nodes or any(path.startswith("Main.") for path in relevant):
        relevant.add("Main")
    children: dict[str, list[str]] = {path: [] for path in relevant}
    for path in relevant:
        if path == "Main":
            continue
        parent = getattr(tree_nodes.get(path), "parent_path", None)
        if parent in relevant:
            children[parent].append(path)
    for values in children.values():
        values.sort()

    if direction == "TB":
        node_separation, rank_separation = "0.36", "0.58"
    elif spacing == "balanced":
        node_separation, rank_separation = "1.55", "0.68"
    else:
        node_separation, rank_separation = "1.05", "0.82"

    lines = [
        "digraph PublicationGraph {",
        "  graph [",
        f"    rankdir={direction},",
        "    compound=true,",
        "    newrank=true,",
        "    clusterrank=local,",
        f"    splines={routing},",
        "    outputorder=nodesfirst,",
        "    remincross=true,",
        "    mclimit=4.0,",
        "    bgcolor=white,",
        "    pad=0.12,",
        f"    nodesep={node_separation},",
        f"    ranksep={rank_separation},",
        "    fontname=Arial,",
        "    charset=\"UTF-8\",",
        "  ];",
        '  node [shape=plain, fontname="Arial"];',
        (
            '  edge [color="#5f7f9f", penwidth=1.5, arrowsize=1.08, '
            'arrowhead=vee, '
            'fontname="Arial"];'
        ),
    ]

    def render_declaration(declaration: PublicationDeclarationLike, indent: str) -> None:
        key = (declaration.node_path, declaration.name)
        main_export = "Main" in propagation.get(key, [])
        exported_scopes = [
            scope for scope in propagation.get(key, []) if scope != "Main"
        ]
        label = _declaration_label(
            declaration,
            main_export=main_export,
            exported_scopes=exported_scopes,
            wrap_chars=wrap_chars,
        )
        attributes = [
            f"label={label}",
            f"id={_dot_quote('decl-' + _safe_identifier(*key))}",
            f"tooltip={_dot_quote(declaration.node_path + '.' + declaration.name)}",
        ]
        link = declaration_links.get(key)
        if link is not None:
            attributes.extend(
                [
                    f"URL={_dot_quote(link)}",
                    'target="_top"',
                ]
            )
        lines.append(
            f"{indent}{declaration_ids[key]} [{', '.join(attributes)}];"
        )

    def render_cluster(path: str, depth: int, indent: str) -> None:
        is_content = path in by_content
        cluster_id = "cluster_" + _safe_identifier(path)
        color = "#9fb3c8" if is_content else "#486581"
        fill = "#f8fbfd" if is_content else "#f3f8fb"
        penwidth = "1.25" if is_content else "1.55"
        label_kind = "Content" if is_content else "Scope"
        display_path = path if path == "Main" else path.rsplit(".", 1)[-1]
        cluster_label = (
            '<<TABLE BORDER="0" CELLBORDER="0" CELLSPACING="0" '
            'CELLPADDING="3"><TR><TD ALIGN="LEFT">'
            f'<FONT POINT-SIZE="11">{html.escape(display_path)} · {label_kind}'
            "</FONT></TD></TR></TABLE>>"
        )
        lines.extend(
            [
                f"{indent}subgraph {_dot_quote(cluster_id)} {{",
                f"{indent}  id={_dot_quote('node-' + _safe_identifier(path))};",
                f"{indent}  label={cluster_label};",
                f"{indent}  color={_dot_quote(color)};",
                f"{indent}  fillcolor={_dot_quote(fill)};",
                f"{indent}  penwidth={penwidth};",
                f"{indent}  style=\"rounded,filled\";",
                f"{indent}  fontcolor=\"#243b53\";",
                f"{indent}  fontsize=11;",
                f"{indent}  margin={18 + min(depth, 3) * 2};",
                f"{indent}  labeljust=l;",
            ]
        )
        if is_content:
            for declaration in by_content[path]:
                render_declaration(declaration, indent + "  ")
        else:
            for child in children.get(path, []):
                render_cluster(child, depth + 1, indent + "  ")
        lines.append(f"{indent}}}")

    if hierarchy == "nested" and "Main" in relevant:
        render_cluster("Main", 0, "  ")
    else:
        for content_path in sorted(by_content):
            render_cluster(content_path, 0, "  ")

    edge_by_pair: dict[tuple[DeclarationKey, DeclarationKey], DependencyKind] = {}
    for consumer, provider, kind in dependency_edges:
        if consumer not in declaration_ids or provider not in declaration_ids:
            continue
        pair = (consumer, provider)
        if pair not in edge_by_pair or kind == "Statement":
            edge_by_pair[pair] = kind
    edge_items = sorted(
        edge_by_pair.items(),
        key=lambda item: (item[0][0], item[0][1], item[1]),
    )
    edge_pairs = [pair for pair, _kind in edge_items]
    outgoing_ports = _distributed_edge_ports(
        edge_pairs,
        endpoint_index=0,
        vertical_order=vertical_order,
    )
    incoming_ports = _distributed_edge_ports(
        edge_pairs,
        endpoint_index=1,
        vertical_order=vertical_order,
    )
    for index, ((consumer, provider), kind) in enumerate(edge_items):
        attributes = [
            f"id={_dot_quote('dependency-' + str(index))}",
            "minlen=1",
        ]
        if direction == "TB":
            attributes.extend(["tailport=s", "headport=n"])
        else:
            tail_port = outgoing_ports[(consumer, provider)]
            head_port = incoming_ports[(consumer, provider)]
            tail_position = {
                "top": "out_top:e",
                "name": "out_upper:e",
                None: "out_middle:e",
                "meta": "out_lower:e",
                "bottom": "out_bottom:e",
            }[tail_port]
            head_position = {
                "top": "in_top:w",
                "name": "in_upper:w",
                None: "in_middle:w",
                "meta": "in_lower:w",
                "bottom": "in_bottom:w",
            }[head_port]
            attributes.extend(
                [
                    f'tailport="{tail_position}"',
                    f'headport="{head_position}"',
                ]
            )
        if kind == "Proof":
            attributes.extend(['style="dashed"', 'color="#b7791f"'])
        lines.append(
            f"  {declaration_ids[consumer]} -> {declaration_ids[provider]} "
            f"[{', '.join(attributes)}];"
        )
    lines.append("}")
    return "\n".join(lines) + "\n"


def _declaration_label(
    declaration: PublicationDeclarationLike,
    *,
    main_export: bool,
    exported_scopes: list[str],
    wrap_chars: int,
) -> str:
    border = "#087ea4" if main_export else "#bcccdc"
    border_width = 3 if main_export else 1
    state_color = "#0f8f88" if declaration.proof_available else "#2563eb"
    name_lines = _wrap_identifier(declaration.name, max_chars=wrap_chars)
    content_width = max(
        140,
        max(len(line) for line in name_lines) * 7 + 20,
        len(declaration.kind + declaration.state) * 5 + 42,
    )
    name_html = '<BR ALIGN="LEFT"/>'.join(
        html.escape(line) for line in name_lines
    )
    annotations: list[str] = []
    if exported_scopes:
        annotations.append(
            "↑ " + " · ".join(scope.rsplit(".", 1)[-1] for scope in exported_scopes)
        )
    annotation_html = ""
    if annotations:
        annotation_html = (
            '<BR ALIGN="LEFT"/><FONT POINT-SIZE="8" COLOR="#087ea4">'
            + html.escape(" · ".join(annotations))
            + "</FONT>"
        )
    return (
        f'<<TABLE BORDER="{border_width}" COLOR="{border}" CELLBORDER="0" '
        'CELLSPACING="0" CELLPADDING="0" BGCOLOR="white">'
        f'<TR><TD PORT="in_top" WIDTH="5" HEIGHT="8" BGCOLOR="{state_color}"></TD>'
        f'<TD ROWSPAN="5"><TABLE BORDER="0" CELLBORDER="0" CELLSPACING="0" '
        'CELLPADDING="8">'
        f'<TR><TD PORT="name" WIDTH="{content_width}" ALIGN="LEFT" BALIGN="LEFT">'
        f'<FONT FACE="Courier" POINT-SIZE="11"><B>{name_html}</B></FONT>'
        "</TD></TR>"
        f'<TR><TD PORT="meta" WIDTH="{content_width}" ALIGN="LEFT" '
        'BALIGN="LEFT">'
        f'<FONT POINT-SIZE="8" COLOR="#627d98">{html.escape(declaration.kind)} / '
        f'{html.escape(declaration.state)}</FONT>{annotation_html}'
        "</TD></TR></TABLE></TD>"
        '<TD PORT="out_top" WIDTH="1" HEIGHT="8"></TD></TR>'
        f'<TR><TD PORT="in_upper" WIDTH="5" HEIGHT="8" BGCOLOR="{state_color}"></TD>'
        '<TD PORT="out_upper" WIDTH="1" HEIGHT="8"></TD></TR>'
        f'<TR><TD PORT="in_middle" WIDTH="5" HEIGHT="8" BGCOLOR="{state_color}"></TD>'
        '<TD PORT="out_middle" WIDTH="1" HEIGHT="8"></TD></TR>'
        f'<TR><TD PORT="in_lower" WIDTH="5" HEIGHT="8" BGCOLOR="{state_color}"></TD>'
        '<TD PORT="out_lower" WIDTH="1" HEIGHT="8"></TD></TR>'
        f'<TR><TD PORT="in_bottom" WIDTH="5" HEIGHT="8" BGCOLOR="{state_color}"></TD>'
        '<TD PORT="out_bottom" WIDTH="1" HEIGHT="8"></TD></TR>'
        "</TABLE>>"
    )


def _wrap_identifier(value: str, *, max_chars: int) -> list[str]:
    if len(value) <= max_chars:
        return [value]
    pieces = [piece for piece in re.split(r"(?<=_)", value) if piece]
    lines: list[str] = []
    current = ""
    for piece in pieces:
        if current and len(current) + len(piece) > max_chars:
            lines.append(current)
            current = piece
        else:
            current += piece
    if current:
        lines.append(current)
    return lines


def _distributed_edge_ports(
    edge_pairs: list[tuple[DeclarationKey, DeclarationKey]],
    *,
    endpoint_index: Literal[0, 1],
    vertical_order: dict[DeclarationKey, float] | None,
) -> dict[tuple[DeclarationKey, DeclarationKey], str | None]:
    grouped: dict[DeclarationKey, list[tuple[DeclarationKey, DeclarationKey]]] = {}
    for pair in edge_pairs:
        grouped.setdefault(pair[endpoint_index], []).append(pair)
    result: dict[tuple[DeclarationKey, DeclarationKey], str | None] = {}
    port_choices = ("top", "name", None, "meta", "bottom")
    choices_by_count: dict[int, tuple[str | None, ...]] = {
        1: (None,),
        2: ("name", "meta"),
        3: ("name", None, "meta"),
        4: ("top", "name", "meta", "bottom"),
        5: port_choices,
    }
    for pairs in grouped.values():
        opposite_index = 1 - endpoint_index
        ordered = sorted(
            pairs,
            key=lambda pair: (
                (
                    -vertical_order.get(pair[opposite_index], 0.0)
                    if vertical_order is not None
                    else 0.0
                ),
                pair,
            ),
        )
        if len(ordered) <= len(port_choices):
            for pair, port in zip(
                ordered,
                choices_by_count[len(ordered)],
                strict=True,
            ):
                result[pair] = port
            continue
        for index, pair in enumerate(ordered):
            bucket = round(index * (len(port_choices) - 1) / (len(ordered) - 1))
            result[pair] = port_choices[bucket]
    return result


def _candidate_score(
    *,
    svg: str,
    width: float,
    height: float,
    declaration_count: int,
    direction: Literal["TB", "LR"],
    routing: Literal["spline"],
    hierarchy: Literal["nested", "content"],
    wrap_chars: int,
) -> float:
    ratio = width / max(height, 1.0)
    target_ratio = 1.6
    aspect_penalty = abs(math.log(max(ratio, 0.01) / target_ratio)) * 120.0
    if ratio > 2.25:
        aspect_penalty += (ratio - 2.25) * 95.0
    elif ratio < 0.85:
        aspect_penalty += (0.85 - ratio) * 95.0
    scale_penalty = math.sqrt(width * height) / max(declaration_count, 1) * 0.12
    hierarchy_penalty = 42.0 if hierarchy == "content" else 0.0
    direction_penalty = 3.0 if direction == "LR" else 0.0
    wrap_penalty = 1.0 if wrap_chars == 64 else 0.0
    routing_penalty = 0.0
    edge_penalty = _edge_route_penalty(svg, direction=direction)
    return (
        aspect_penalty
        + scale_penalty
        + hierarchy_penalty
        + direction_penalty
        + wrap_penalty
        + routing_penalty
        + edge_penalty
    )


def _edge_route_penalty(value: str, *, direction: Literal["TB", "LR"]) -> float:
    groups = re.findall(
        r'<g id="dependency(?:&#45;|-)\d+" class="edge">(.*?)</g>',
        value,
        flags=re.DOTALL,
    )
    penalty = 0.0
    for group in groups:
        match = re.search(r'<path[^>]+d="([^"]+)"', group)
        if match is None:
            continue
        numbers = [
            float(item)
            for item in re.findall(r"-?(?:\d+(?:\.\d*)?|\.\d+)", match.group(1))
        ]
        points = list(zip(numbers[0::2], numbers[1::2], strict=False))
        primary = [point[0 if direction == "LR" else 1] for point in points]
        if len(primary) < 2:
            continue
        forward = max(primary[-1] - primary[0], 1.0)
        backward = sum(
            max(previous - current, 0.0)
            for previous, current in zip(primary, primary[1:], strict=False)
        )
        penalty += min(backward / forward, 3.0) * 32.0
    return penalty


def _svg_dimensions(value: str) -> tuple[float, float] | None:
    match = re.search(
        r'viewBox="[-0-9.]+\s+[-0-9.]+\s+([0-9.]+)\s+([0-9.]+)"',
        value,
    )
    if match is None:
        return None
    return (float(match.group(1)), float(match.group(2)))


def _svg_declaration_vertical_order(
    value: str,
    *,
    declaration_ids: dict[DeclarationKey, str],
) -> dict[DeclarationKey, float]:
    """Read declaration centers from an initial Graphviz SVG layout."""

    key_by_id = {identifier: key for key, identifier in declaration_ids.items()}
    result: dict[DeclarationKey, float] = {}
    for match in re.finditer(
        r'<g id="([^"]+)" class="node">(.*?)</g>',
        value,
        flags=re.DOTALL,
    ):
        key = key_by_id.get(html.unescape(match.group(1)))
        if key is None:
            continue
        polygon = re.search(r'<polygon[^>]+points="([^"]+)"', match.group(2))
        if polygon is None:
            continue
        coordinates = [
            tuple(float(component) for component in point.split(",", 1))
            for point in polygon.group(1).split()
            if "," in point
        ]
        if not coordinates:
            continue
        y_values = [coordinate[1] for coordinate in coordinates]
        result[key] = (min(y_values) + max(y_values)) / 2
    return result


def _normalize_graphviz_svg(value: str, *, title: str) -> str:
    value = re.sub(r"<\?xml[^>]*>\s*", "", value, count=1)
    value = re.sub(r"<!DOCTYPE[^>]*(?:\[[\s\S]*?\]\s*)?>\s*", "", value, count=1)
    value = value.replace(
        "<svg ",
        f'<svg role="img" aria-label="{html.escape(title, quote=True)}" ',
        1,
    )
    return value.rstrip() + "\n"


def _render_empty_svg(title: str) -> str:
    escaped = html.escape(title)
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="760" height="160" '
        'viewBox="0 0 760 160" role="img">\n'
        f"  <title>{escaped}</title>\n"
        '  <rect width="760" height="160" fill="#ffffff"/>\n'
        f'  <text x="28" y="42" font-family="ui-sans-serif, system-ui" '
        f'font-size="20" font-weight="700" fill="#102a43">{escaped}</text>\n'
        '  <text x="28" y="86" font-family="ui-sans-serif, system-ui" '
        'font-size="14" fill="#627d98">No exported declarations.</text>\n'
        "</svg>\n"
    )


def _safe_identifier(*parts: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "-", "--".join(parts)).strip("-")


def _dot_quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


__all__ = [
    "PublicationGraphvizUnavailable",
    "render_publication_graph_svg",
]
