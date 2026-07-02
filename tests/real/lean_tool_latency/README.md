# Lean Tool Latency Benchmarks

This directory contains benchmark-style real tests for comparing the current
Lean Constellation Lean tool paths.

The tests do not enforce fixed performance thresholds. They verify that each
measured operation returns the expected success/failure shape, then export JSON
and Markdown artifacts for later analysis.

Useful environment variables:

- `LEAN_CONSTELLATION_LEAN_LATENCY_ARTIFACT_DIR`: optional output directory for
  copied benchmark artifacts. When omitted, artifacts are written only under the
  pytest `tmp_path`.
- `LEAN_CONSTELLATION_LEAN_LATENCY_ITERATIONS`: repeat count for repeatable
  operations. Defaults to `2`.
- `LEAN_CONSTELLATION_LEAN_LATENCY_TIMEOUT`: timeout in seconds for Lean/Lake
  calls. Defaults to `180`.
- `LEAN_CONSTELLATION_REAL_LEAN_TEMPLATE_ROOT`: optional Mathlib template repo.
  Defaults to `/root/lean_projects/lean_template_428` or `_427` when present.
- `LEAN_CONSTELLATION_REAL_TOOLKIT_BASE_URL`: live Lean MCP Toolkit base URL for
  the live Toolkit benchmark.

Example local run:

```bash
PYTHONPATH=src:/root/code/agent-runtime-kit/src \
LEAN_CONSTELLATION_LEAN_LATENCY_ARTIFACT_DIR=/root/code/lean-constellation/data/lean_tool_latency/latest \
python -m pytest tests/real/lean_tool_latency -m lean_latency -q -s --tb=short
```

Example live Toolkit run:

```bash
PYTHONPATH=src:/root/code/agent-runtime-kit/src:/root/code/lean-mcp-toolkit/src \
LEAN_CONSTELLATION_LEAN_LATENCY_ARTIFACT_DIR=/root/code/lean-constellation/data/lean_tool_latency/latest \
LEAN_CONSTELLATION_REAL_TOOLKIT_BASE_URL=http://127.0.0.1:18083 \
python -m pytest tests/real/lean_tool_latency/test_lean_tool_latency_matrix.py::test_live_toolkit_latency_matrix -q -s --tb=short
```
