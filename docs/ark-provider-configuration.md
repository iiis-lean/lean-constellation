# ARK Agent Provider Configuration

Lean Constellation binds each application `AgentType` to an ARK Provider and
Home. `codex` is the default Provider. A deployment may select
`claude_code`, `pi`, `openai_agents`, or `opencode` globally, and may override
that selection for individual AgentTypes.

```toml
default_agent_provider_type = "codex"
```

The equivalent environment variable is
`LEAN_CONSTELLATION_DEFAULT_AGENT_PROVIDER_TYPE`. A per-AgentType override
takes precedence over the global default:

```toml
[agent_home_overrides.RepoFormatDiscoveryAgent]
provider_type = "opencode"
api_provider = "deepseek"
api_mode = "chat_completions"
model = "deepseek-chat"
required_env = ["DEEPSEEK_API_KEY"]

[agent_home_overrides.RepoFormatDiscoveryAgent.provider_options]
binary_path = "opencode"

[agent_home_overrides.SourceCorpusPrepareAgent]
provider_type = "claude_code"
api_provider = "deepseek"
api_mode = "anthropic_messages"
model = "deepseek-chat"

[agent_home_overrides.SourceCorpusPrepareAgent.provider_options]
setting_sources = ["user"]
permission_mode = "bypassPermissions"
```

Provider configuration has three layers:

- `provider_type` selects the runtime adapter independently of the model.
- `api_provider`, `api_mode`, `model`, model limits, and reasoning settings
  describe the neutral backend identity retained in ARK results and usage.
- `base_config_path`, `config_overrides`, and `provider_options` are projected
  by the selected Provider's Home renderer. Credentials must be referenced by
  environment-variable name or provider-owned auth files; inline secrets are
  rejected.

Supported `api_mode` values are determined by the Provider adapter. In
particular, OpenAI Agents supports both `responses` and `chat_completions`.
Provider-native compaction is available only when the selected endpoint and
Home capability support it; Chat Completions configurations must currently
declare compaction unsupported. LC does not silently replace this with an
application-owned summarizer.

Pi and OpenCode are subprocess Providers and require their pinned CLIs to be
available on the host. Claude Code additionally requires the ARK `claude`
extra, and OpenAI Agents requires the `openai-agents` extra. LC exposes these
as `provider-claude` and `provider-openai-agents` installation extras.

Interactive `NEEDS_INPUT` handling is intentionally not enabled in the first
LC integration. Provider adapters retain capability metadata for a later
neutral approval/input lifecycle; a configured run must fail closed instead
of treating an input request as successful completion.

Application code that needs a custom OpenAI Agents factory can pass an
application-built ARK `ProviderRegistry` to `create_app_runtime_services` or
`create_app_runtime_from_config`. The production config auto-assembler uses
the built-in `lean_constellation/default` factory.

LC and ARK now read and write only the provider-neutral schema v3 Agent, Home,
session-locator, artifact-locator, result, and snapshot records. Records from
the removed Codex-specific schemas are not migrated or accepted. Start a fresh
runtime root when upgrading an existing workspace; do not copy the previous
`.agent_runtime` directory into the new workspace.
