from pathlib import Path

from lean_constellation.agents import build_agent_type_specs
from lean_constellation.app.bootstrap import materialize_agent_home
from lean_constellation.app.config import LeanAppConfig, AgentHomeOverrideAppConfig
from lean_constellation.app.runtime import create_app_runtime_from_config
from lean_constellation.app.agent_provider_config import provider_options_from_override


def test_grok_options_and_all_role_homes(tmp_path: Path) -> None:
    options = provider_options_from_override("grok", AgentHomeOverrideAppConfig(
        provider_type="grok", model="grok-4.6", model_reasoning_effort="low",
        provider_options={"auth_json_path": None},
    ))
    assert options.model == "grok-4.6"
    assert options.reasoning_effort == "low"
    config = LeanAppConfig(workspace_root=tmp_path, materialize_agent_homes=False, default_agent_provider_type="grok")
    runtime = create_app_runtime_from_config(config)
    assert {b.provider_type for b in runtime.ark.agent_service.provider_registry.list()} == {"grok"}
    specs = [s.model_copy(update={"home_type": "grok"}) for s in build_agent_type_specs()]
    for spec in specs:
        result = materialize_agent_home(runtime, spec.agent_type, provider_type="grok",
            agent_type_specs=specs, provider_options=options, mcp_http_base_url="http://127.0.0.1:1")
        assert result.ok, (spec.agent_type, result.issues)
        paths = runtime.ark.agent_service.home_service.get_skill_paths("grok", spec.agent_type)
        assert set(paths) == set(spec.skill_keys)
        assert all(path.joinpath("SKILL.md").is_file() for path in paths.values())
