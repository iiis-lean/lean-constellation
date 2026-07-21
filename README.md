# Lean Constellation

Lean Constellation is a multi-repository coordination runtime for Lean
formalization workflows.

It is intended to coordinate a graph of Lean repositories, keep their
dependencies explicit, assign repo-level coordinators, run node-scoped
formalization tasks, and preserve reproducible snapshots without carrying over
the heavier workflow machinery from earlier Lean Steward prototypes.

## Current Status

The runtime now includes repo-local Flow/Step scheduling, Agent Home
materialization, checkpoint/restore integration, and ARK 0.2 multi-provider
bindings. Codex remains the compatibility default; Claude Code, Pi, OpenAI
Agents, and OpenCode can be selected per AgentType. See
[ARK Agent Provider Configuration](docs/ark-provider-configuration.md).

## Repository Structure

```text
lean-constellation/
├── src/
├── tests/
├── docs/
├── dev_docs/
│   ├── working_on.md
│   ├── design/
│   └── dev_records/
├── data/
├── configs/
├── README.md
├── AGENTS.md
└── .gitignore
```

## Development Notes

- Public, reusable documentation belongs in `docs/`.
- Local design notes, working plans, and daily records belong in `dev_docs/`.
- Real local configuration files are ignored; keep only example templates in
  `configs/`.
