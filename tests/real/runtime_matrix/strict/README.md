# Runtime Matrix Strict Coverage Tests

本目录存放第一层 Runtime Matrix 的 strict coverage 扩展测试。它不能用 baseline 的静态声明替代完成依据，必须逐步产生实际执行 evidence。

当前已经落地：

- `test_strict_evidence_manifest.py`：注册表 surface 读取和 missing report smoke。
- `test_zz_strict_actual_evidence_manifest.py`：session 级实际 evidence 审计，断言当前 strict suite 覆盖全部 Flow / LogicStep / AgentStep / submit tool 和 202 个本地 implemented application ToolCase；当前 ToolCase 表另有 38 个 pending fixture 和 12 个 env-gated 工具；同时审计 checkpointed write ToolCase 都有实际调用和断言摘要。
- `test_flow_step_submit_strict_matrix.py`：repo format 和 resource curator 首批分支 evidence。
- `test_real_lean_embedded_decl_round.py`：真实 `LakeCommandClient` 嵌入完整 `decl_graph_round` formal path。
- `test_application_tool_sweep_full.py`：ToolCase 表 parity 和 core ToolSweep 分区真实 MCP 调用。
- `test_application_tool_sweep_decl_graph.py`：覆盖 DeclGraph strategy / round / catalog / readiness ToolCase。
- `test_application_tool_sweep_decl_stage.py`：真实 `LakeCommandClient` 覆盖 DeclStage NL/formal file、formal diagnostics / policy 和 review mark ToolCase。
- `test_application_tool_sweep_scope_export.py`：覆盖 scope export write 和 scope interface bind/unbind ToolCase。
- `test_application_tool_sweep_local_boundaries.py`：使用真实 `LakeCommandClient` 覆盖 `check_mathlib_name`，并把 local-boundary ToolCase 从 broad core sweep 中独立出来。
- `test_application_tool_sweep_live_env.py`：显式开启 live-env 后，通过真实 GitHub CLI 和 live Lean MCP Toolkit HTTP server 覆盖 8 个 env-gated application tools 的 MCP 调用链。
- `test_application_tool_sweep_live_material_acquisition.py`：显式开启 live material gate 后，通过真实 web 和 arXiv 网络下载覆盖 material acquisition / extraction 工具链。
- `tool_sweep_partitions.py`：把 implemented ToolCase 分配到 core / DeclGraph / DeclStage formal / scope export 等执行分区。
- `test_real_codex_agent_resource_matrix.py`：真实 Codex SDK/CLI 覆盖 controlled Coordinator / ResourceCurator / StatementFormalWorker / ProofFormalWorker / AdapterDeclCatalog / MathlibRecon Agent 资源、MCP tool 和 submit 验证。
- `scripted_provider.py`：以标准 Provider bundle 覆盖 deterministic Agent run、MCP action、query 与 artifact snapshot/restore 合同。
- `test_repo_preparation_strict_matrix.py`：native / adapter preparation Flow、Step、submit 分支 evidence。
- `test_coordinator_content_recon_strict_matrix.py`：Coordinator、content node task 和 recon child Flow 分支 evidence。
- `test_decl_graph_strict_branches.py`：DeclGraph review rejected -> worker blocked 分支和 delete/normalize 分支 evidence。

当前完成状态：

- 默认 strict suite 当前 registry surface 为 258 个 application tools，其中 202 个 ToolCase 标记 implemented，44 个 pending fixture，12 个 env-gated；implemented 部分审 Flow / LogicStep / AgentStep / submit / checkpointed write evidence。
- `test_application_tool_sweep_live_env.py` 在 live Toolkit + GitHub 环境中覆盖 8 个 env-gated ToolCase；`search_arxiv_theorems` 会真实到达 live Toolkit provider，并在 LeanSearch theorem endpoint 返回 500 时通过真实 arXiv e-print source fallback 返回 theorem candidate。
- `test_real_codex_agent_resource_matrix.py` 在真实 Codex SDK/CLI 环境中覆盖六个重点 Agent，并写入 `*_transcript.json` 供人工复查 normalized final response、provider artifact locator 和 trace report。

baseline 测试保留在 sibling 目录 `../baseline/`，不能作为 strict 完成依据。

完成标准以 `dev_docs/implementation/runtime_matrix_testing/strict_coverage/README.md` 和 strict 任务索引为准。
