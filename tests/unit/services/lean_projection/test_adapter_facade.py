from tests.unit_services_helpers import make_runtime

from pathlib import Path

from lean_constellation.services.foundation import FoundationService, ServiceResult
from lean_constellation.services.lean_projection import AdapterFacadeComponent, AdapterModuleListView


class FakeAdapterFacadeProvider:
    def __init__(self, foundation: FoundationService, *, active_modules: list[str], visible_modules: list[str]) -> None:
        self.foundation = foundation
        self.active_modules = active_modules
        self.visible_modules = visible_modules

    def list_active_adapter_modules(self, repo_root: Path) -> ServiceResult[AdapterModuleListView]:
        del repo_root
        return self.foundation.ok(
            AdapterModuleListView(
                modules=self.active_modules,
                summary=f"{len(self.active_modules)} active modules.",
            )
        )

    def list_visible_upstream_modules(self, repo_root: Path) -> ServiceResult[AdapterModuleListView]:
        del repo_root
        return self.foundation.ok(
            AdapterModuleListView(
                modules=self.visible_modules,
                summary=f"{len(self.visible_modules)} visible modules.",
            )
        )


class FailingAdapterFacadeProvider:
    def __init__(self, foundation: FoundationService, *, fail_active: bool = False, fail_visible: bool = False) -> None:
        self.foundation = foundation
        self.fail_active = fail_active
        self.fail_visible = fail_visible

    def list_active_adapter_modules(self, repo_root: Path) -> ServiceResult[AdapterModuleListView]:
        del repo_root
        if self.fail_active:
            return self.foundation.fail(
                self.foundation.issue("adapter_active_provider_failed", "Active adapter module provider failed.")
            )
        return self.foundation.ok(AdapterModuleListView(modules=["Upstream.Basic"], summary="active"))

    def list_visible_upstream_modules(self, repo_root: Path) -> ServiceResult[AdapterModuleListView]:
        del repo_root
        if self.fail_visible:
            return self.foundation.fail(
                self.foundation.issue("adapter_visible_provider_failed", "Visible upstream module provider failed.")
            )
        return self.foundation.ok(AdapterModuleListView(modules=["Upstream.Basic"], summary="visible"))


def _component(active_modules: list[str], visible_modules: list[str]) -> AdapterFacadeComponent:
    runtime = make_runtime()
    foundation = runtime.foundation
    provider = FakeAdapterFacadeProvider(foundation, active_modules=active_modules, visible_modules=visible_modules)
    return AdapterFacadeComponent(runtime, provider=provider)


def test_render_adapter_interfaces_deduplicates_sorts_and_uses_public_import(tmp_path: Path) -> None:
    component = _component(
        active_modules=["Upstream.Topology.Basic", "Upstream.Algebra.Basic", "Upstream.Topology.Basic"],
        visible_modules=["Upstream.Topology.Basic", "Upstream.Algebra.Basic"],
    )

    result = component.render_adapter_interfaces(tmp_path)

    assert result.ok
    assert result.value is not None
    assert "Adapter facade: Main.Interfaces" in result.value
    assert result.value.index("public import Upstream.Algebra.Basic") < result.value.index("public import Upstream.Topology.Basic")
    assert result.value.count("public import Upstream.Topology.Basic") == 1


def test_render_adapter_interfaces_allows_empty_active_module_set(tmp_path: Path) -> None:
    component = _component(active_modules=[], visible_modules=["Upstream.Basic"])

    result = component.render_adapter_interfaces(tmp_path)

    assert result.ok
    assert result.value is not None
    assert "public import" not in result.value


def test_render_rejects_invalid_or_not_visible_modules(tmp_path: Path) -> None:
    invalid = _component(active_modules=["../Bad"], visible_modules=["../Bad"])
    invalid_result = invalid.render_adapter_interfaces(tmp_path)
    assert not invalid_result.ok
    assert invalid_result.issues[0].kind == "adapter_active_module_invalid"

    missing = _component(active_modules=["Upstream.Missing"], visible_modules=["Upstream.Basic"])
    missing_result = missing.render_adapter_interfaces(tmp_path)
    assert not missing_result.ok
    assert missing_result.issues[0].kind == "adapter_module_not_visible"


def test_adapter_facade_uses_exact_provider_visibility_without_import_graph_inference(tmp_path: Path) -> None:
    component = _component(active_modules=["Upstream.Algebra.Group.Basic"], visible_modules=["Upstream.Algebra"])

    render = component.render_adapter_interfaces(tmp_path)
    visible = component.check_adapter_module_visible(tmp_path, module="Upstream.Algebra.Group.Basic")

    assert not render.ok
    assert render.issues[0].kind == "adapter_module_not_visible"
    assert visible.ok
    assert visible.value is not None
    assert visible.value.passed is False
    assert visible.value.issues[0].kind == "adapter_module_not_visible"


def test_adapter_facade_provider_failures_are_propagated_without_writing_projection(tmp_path: Path) -> None:
    foundation = make_runtime().foundation
    runtime = make_runtime()
    active_failure = AdapterFacadeComponent(
        runtime,
        provider=FailingAdapterFacadeProvider(foundation, fail_active=True),
    )
    visible_failure = AdapterFacadeComponent(
        runtime,
        provider=FailingAdapterFacadeProvider(foundation, fail_visible=True),
    )

    active_result = active_failure.render_adapter_interfaces(tmp_path)
    visible_result = visible_failure.refresh_adapter_interfaces(tmp_path)

    assert not active_result.ok
    assert active_result.issues[0].kind == "adapter_active_provider_failed"
    assert not visible_result.ok
    assert visible_result.issues[0].kind == "adapter_visible_provider_failed"
    assert not (tmp_path / "Main" / "Interfaces.lean").exists()


def test_check_adapter_module_visible_passes_and_fails(tmp_path: Path) -> None:
    component = _component(active_modules=[], visible_modules=["Upstream.Basic"])

    passed = component.check_adapter_module_visible(tmp_path, module="Upstream.Basic")
    assert passed.ok
    assert passed.value is not None
    assert passed.value.passed is True

    missing = component.check_adapter_module_visible(tmp_path, module="Upstream.Other")
    assert missing.ok
    assert missing.value is not None
    assert missing.value.passed is False
    assert missing.value.issues[0].kind == "adapter_module_not_visible"

    invalid = component.check_adapter_module_visible(tmp_path, module="Bad Module")
    assert not invalid.ok
    assert invalid.issues[0].kind == "adapter_module_invalid"


def test_refresh_and_check_adapter_interfaces_sync(tmp_path: Path) -> None:
    component = _component(active_modules=["Upstream.Basic"], visible_modules=["Upstream.Basic"])

    missing = component.check_adapter_interfaces_sync(tmp_path)
    assert missing.ok
    assert missing.value is not None
    assert missing.value.passed is False
    assert missing.value.issues[0].kind == "adapter_interfaces_missing"

    refreshed = component.refresh_adapter_interfaces(tmp_path)
    assert refreshed.ok
    assert refreshed.value is not None
    assert refreshed.value.changed is True
    path = Path(refreshed.value.path)
    assert path == tmp_path / "Main" / "Interfaces.lean"
    assert "public import Upstream.Basic" in path.read_text(encoding="utf-8")

    second = component.refresh_adapter_interfaces(tmp_path)
    assert second.ok
    assert second.value is not None
    assert second.value.changed is False

    synced = component.check_adapter_interfaces_sync(tmp_path)
    assert synced.ok
    assert synced.value is not None
    assert synced.value.passed is True

    path.write_text("public import Other.Module\n", encoding="utf-8")
    stale = component.check_adapter_interfaces_sync(tmp_path)
    assert stale.ok
    assert stale.value is not None
    assert stale.value.passed is False
    assert stale.value.issues[0].kind == "adapter_interfaces_stale"
