"""Shared service-runtime helpers for unit tests."""

from __future__ import annotations

import sys
from pathlib import Path

from agent_runtime_kit.runtime import ARKServices

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from lean_constellation.services import LeanProviderOverrides, LeanRuntimeServices, create_test_runtime_services


def make_runtime(
    *,
    external_config: object | None = None,
    external_overrides: dict[str, object] | None = None,
    providers: LeanProviderOverrides | None = None,
) -> LeanRuntimeServices:
    """Create a real Lean runtime service graph backed by real ARKServices."""

    return create_test_runtime_services(
        ark_services=ARKServices(),
        external_config=external_config,
        external_overrides=external_overrides,
        providers=providers,
    )
