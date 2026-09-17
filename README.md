# zhenyun-script-platform-mcp

甄云二开脚本平台的独立 MCP 服务。它读取并解码脚本、把**未保存源码**提交到 DEV
GraalJS Runtime 调试，并在用户明确要求后执行带版本保护的保存或 Adapter 安全部署。

本项目与 `zhenyun-pangu-mcp` 没有代码依赖：本项目只负责 Script Platform 生命周期；日志、
数据库、GitLab、Choerodon 和知识库仍由 Pangu 提供，Codex / CodeBuddy 在 Agent 层编排二者。
Pangu 的 `search_adapter_scripts` / `search_standalone_scripts` 可以在精确身份未知时发现候选；
找到租户、编码和运行服务后，当前源码、版本与状态必须回到本 MCP 的 `get` 工具读取。

## 领域模型

Script Platform 有两套互不混用的模型：

- Independent Script：`API_PRE`、`API_POST`、`API_PUBLISH`。其输入是 HTTP Context 或发布
  API 的输入。
- Adapter：由 `taskCode + runningService + applyTenantNum` 定位，Header 下可以有多条 Line，
  输入通常是 Java DTO/VO/Map/List。

平台文本字段 `content`、`scriptContent`、`contentInput`、`inputContent` 均按
`plain text -> UTF-16BE -> Base64` 编码。MCP 在边界内统一编解码，调用方只处理明文源码和
JSON。

## 七个 MCP 工具

| 工具 | 行为 | 是否改变平台状态 |
| --- | --- | --- |
| `independent_script_get` | 精确查询、解码源码及测试 Input | 否 |
| `independent_script_debug` | 用当前未保存源码调用 DEV Debug Runtime | 否 |
| `independent_script_save` | 完整记录 PUT、版本检查、回读校验 | **是** |
| `adapter_get` | 查询 Header 并读取所有 Line | 否 |
| `adapter_debug` | 带当前 `scriptVersion` 调试指定 Line | 否 |
| `adapter_extract_input` | 从调用方提供的日志文本中平衡提取 JSON | 否 |
| `adapter_deploy` | 停用（若需要）、重新读取、保存、校验、恢复状态 | **是** |

核心原则是 **Debug First, Save Last**。Debug 工具不会保存、停用或启用任何脚本。只有用户
明确说“保存 / 发布 / 部署 / 更新到 DEV”后才应调用两个写工具。

## 安装与运行

要求 Python 3.11+ 和 `uv`：

```bash
cp .env.example .env
uv sync
uv run zhenyun-script-platform-mcp
```

也可以从任意目录运行：

```bash
uv run --project /ABSOLUTE/PATH/zhenyun-script-platform-mcp \
  zhenyun-script-platform-mcp
```

## 配置

| 环境变量 | 必需 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `SCRIPT_PLATFORM_BASE_URL` | 是 | 无 | Script Platform 网关根地址，禁止硬编码 |
| `SCRIPT_PLATFORM_BEARER_TOKEN` | 否 | 空 | Bearer Token |
| `SCRIPT_PLATFORM_COOKIE` | 否 | 空 | 仅在目标环境确实需要时配置 |
| `SCRIPT_PLATFORM_MENU_ID` | 否 | 空 | 可选 `H-Menu-Id` |
| `SCRIPT_PLATFORM_TIMEOUT` | 否 | `30` | HTTP 超时秒数 |
| `SCRIPT_PLATFORM_VERIFY_SSL` | 否 | `true` | 是否校验证书 |
| `SCRIPT_PLATFORM_ALLOW_WRITE` | 否 | `false` | 两个写工具总开关 |
| `SCRIPT_PLATFORM_ALLOWED_HOSTS` | 否 | 空 | 允许写入的主机逗号列表；建议始终配置 DEV 主机 |

写操作需要 `ALLOW_WRITE=true`，并且配置了 `ALLOWED_HOSTS` 时 Base URL 主机必须在白名单
内。不要把真实 Token、Cookie 或业务 Input 提交到仓库。

## Fixture 规则

保存的测试 Input 被分类为：

- `AVAILABLE`：有效 JSON，可用于调试。
- `PLACEHOLDER`：例如 `{"ANYTHING":"string"}`，不会作为有效业务 Fixture 返回。
- `INVALID`：编码或 JSON 无效。
- `MISSING`：未保存测试 Input。

调试优先使用工具显式传入的 `raw_input`，其次使用有效的保存 Fixture。最近真实 Input 应由
Pangu 查询日志，再把日志文本交给 `adapter_extract_input`；本 MCP 不自行访问 Loki/SLS，也
不会编造复杂业务 DTO。

## Adapter 部署生命周期

`adapter_deploy` 的固定流程：

1. GET 最新 Header/Lines，并校验预期 Header/Line 版本。
2. 若原状态启用，则临时停用。
3. 停用后再次 GET，基于最新完整对象构造保存 payload。
4. 只替换目标 Line 的 `scriptContent`，调用 `adaptor-save`。
5. 再次 GET 并逐字校验解码后的源码。
6. 仅当校验成功且原状态启用时重新启用，再 GET 验证最终状态。

保存失败会尝试恢复原启用状态；保存成功但重新启用失败会返回
`RE_ENABLE_FAILED + requires_manual_attention=true`；源码回读不一致会保持停用并返回
`SAVE_VERIFICATION_FAILED`。

## 与 Pangu 协作

典型流程：

```text
adapter_get
  -> Codex 修改明文源码
  -> Pangu 查询最近日志
  -> adapter_extract_input
  -> adapter_debug（可重复）
  -> Pangu 查询 DEV DB 验证
  -> 用户明确确认
  -> adapter_deploy
```

## Codex / CodeBuddy 配置

```json
{
  "mcpServers": {
    "zhenyun-script-platform": {
      "command": "uv",
      "args": [
        "run",
        "--project",
        "/ABSOLUTE/PATH/zhenyun-script-platform-mcp",
        "zhenyun-script-platform-mcp"
      ],
      "env": {
        "SCRIPT_PLATFORM_BASE_URL": "https://gateway.dev.example.com",
        "SCRIPT_PLATFORM_BEARER_TOKEN": "",
        "SCRIPT_PLATFORM_ALLOW_WRITE": "false",
        "SCRIPT_PLATFORM_ALLOWED_HOSTS": "gateway.dev.example.com"
      }
    }
  }
}
```

更完整的执行流、失败恢复和安全边界见 [docs/architecture.md](docs/architecture.md)。

## 开发验证

```bash
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
```

单元测试全部使用 Fake/Mock HTTP，不访问 DEV。真实 Smoke Test 只允许 GET 和 Debug，且必须
提供有效 Fixture；不得自动执行 Save/Deploy。
