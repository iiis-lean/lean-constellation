# Runtime Matrix Tests

本目录用于第一层 Runtime Matrix 测试。

## 当前结构

- `admin_helpers.py`、`fixtures.py`、`scripted_provider.py`
  - Runtime Matrix 共享测试设施，供 baseline 和 strict coverage 共同复用。
- `baseline/`
  - 上一轮 Runtime Matrix baseline 测试。
  - 这些测试保留，因为它们已经覆盖了真实 ARK runtime、Flow/Step/AgentStep、ToolFacade/MCP、submit gateway 的大量代表路径。
  - 但 baseline 中的 `coverage_static.py` 是静态声明覆盖，不是实际执行证据，不能作为第一层完成标准。

## Strict Coverage 要求

后续 strict coverage 测试必须新增实际执行 evidence 层，并以 evidence 证明：

- 所有业务 Flow 实际运行；
- 所有 LogicStep 实际执行；
- 所有 AgentStep 实际启动；
- 所有 submit tool 通过真实 AgentStep + MCP/ToolFacade submit gateway 进入；
- 所有 application tool 至少有一次真实 MCP 调用和效果断言；
- 重点 Agent 使用真实 Codex SDK 验证 prompt / instruction / skills / env / MCP tools。

默认不允许把 `schema_only_with_reason`、静态 manifest、env-gated skip 计为完成。
