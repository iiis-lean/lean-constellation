# Grok Build provider

LC supports the ARK Grok adapter for Grok Build CLI 1.0.30 (the adapter verifies
the pinned binary). Use a fresh runtime/Home when selecting a different provider;
existing Codex sessions are not converted into Grok sessions.

```toml
default_agent_provider_type = "grok"

[agent_home_overrides.CoordinatorAgent]
model = "grok-4.6"
model_reasoning_effort = "low"

[agent_home_overrides.CoordinatorAgent.provider_options]
binary_path = "/root/.grok/bin/grok"
auth_json_path = "/root/.grok/auth.json"
```

Provider options apply per AgentType; other roles use adapter defaults unless
overridden. Keep authentication local. Do not supply Codex base configuration or
raw config overrides for Grok. LC installs its SkillSpecs and two MCP views through
ARK; dynamic Step/Flow/Agent identity is evaluated for each new Grok process.

Role permission defaults follow LC's existing file-writing and web-research
role sets, including controlled role inheritance. All roles receive read tools;
file-writing roles additionally receive `search_replace` and `run_terminal_cmd`;
research roles receive `web_search` and `web_fetch`. An explicit `tools` option
replaces these defaults. Native terminal permission is not an OS sandbox.

ARK owns skill materialization; LC queries manifest-recorded skill paths through
`HomeService.get_skill_paths`. Existing non-Grok renderers that do not enumerate
skills retain LC's legacy display-path fallback pending the provider-boundary
refactor.

## Verification and limits

Real opt-in tests (benchmark environment):

```bash
ARK_RUN_REAL_GROK=1 python -m pytest tests/real/runtime_matrix/test_real_grok_lc.py -q -s
```

These exercise real Grok application/submission calls, Coordinator closeout,
formal-worker Lean compilation and stage transition, and resumed LC submission
after manual compact, recovery fork and native snapshot restore. Fixtures are
isolated; deterministic adjacent steps do not establish a fully autonomous
end-to-end theorem run. The formal check uses real Lean, not a live Toolkit HTTP
service; that service and extended autonomous runs remain separate deployment
checks.

Grok supports manual compact, but ARK does not expose a verified context-usage
ratio. LC's 80% conditional compact therefore skips unavailable measurements;
native Grok automatic compaction is distinct and has not been stress-tested by
these bounded tests. Historical-turn fork and queued follow-up are unsupported;
latest-session fork and sequential resume are supported. No existing production
run is changed by this integration.
