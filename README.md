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

## MCP 工具

脚本生命周期保留 8 个高层工具：

| 工具 | 行为 | 是否改变平台状态 |
| --- | --- | --- |
| `independent_script_create` | 编码源码/测试 Input 后创建独立脚本，并提示可能的连带资源 | **是** |
| `independent_script_get` | 精确查询、解码源码及测试 Input | 否 |
| `independent_script_debug` | 用当前未保存源码调用 DEV Debug Runtime | 否 |
| `independent_script_save` | 完整记录 PUT、版本检查、回读校验 | **是** |
| `adapter_get` | 查询 Header 并读取所有 Line | 否 |
| `adapter_debug` | 带当前 `scriptVersion` 调试指定 Line | 否 |
| `adapter_extract_input` | 从调用方提供的日志文本中平衡提取 JSON | 否 |
| `adapter_deploy` | 停用（若需要）、重新读取、保存、校验、恢复或按 `enable` 启用状态 | **是** |

从 WebOps 参考实现吸收的增量能力按现有风格收敛为 16 个工具：

| 分组 | 工具 | 说明 |
| --- | --- | --- |
| 上下文 | `platform_context_get` / `platform_capabilities_list` | 返回脱敏认证状态、环境边界和已实现能力 |
| 需求资产发现 | `platform_requirement_artifacts_search` | 按需求号聚合搜索 Adapter、Independent、CodeBlock、QueryBlock、API 发布和改写候选；不返回完整源码 |
| 通用读取 | `platform_resource_search` / `platform_resource_get` | 查询 14 类封闭资源，不接受任意表名或 URL |
| 元数据 | `platform_definition_get` / `platform_relations_get` / `platform_api_point_list` | 11 类定义可用资源的字段定义、脚本引用关系和 API 挂载点 |
| 通用写入 | `platform_resource_create` / `platform_resource_save` / `platform_resource_delete` | 10 类已验证 Rel-Table CRUD；更新/删除强制版本条件 |
| 表级动作 | `platform_table_action` | 只允许表与 actionId 的已验证组合；调度和连通性测试可能有真实副作用 |
| Adapter 生命周期 | `adapter_create` / `adapter_update` / `adapter_toggle` / `adapter_delete` | 创建、元数据更新、显式启停和物理删除；源码仍用 `adapter_deploy` |

通用资源类型覆盖 Adapter/Independent Script 全览、Topic 消费端、API 发布、API 改写与挂载、
功能数据导入配置、调度、常量、OutBound 白名单、CodeBlock、QueryBlock、脚本日志和 Adapter
事件编码注册表。完整迁移取舍见 [WebOps 能力迁移说明](docs/webops-migration.md)。

`platform_requirement_artifacts_search` 用于历史需求增量交付：它把规范化需求号作为描述字段过滤，
在一次调用中返回六类资源的脱敏候选身份和扫描完整性；调用方随后仍须按类型调用精确 `get`。
历史资源可能没有在描述中记录需求号，因此零结果或扫描不完整都不能证明资源不存在。通用
`platform_resource_search(text=...)` 对 Rel-Table 使用 `description`，对 Adapter 列表也使用平台已
验证的 `description` 参数，而不是假定所有端点都支持名为 `text` 的参数。

核心原则是 **Debug First, Save Last**。Debug 工具不会保存、停用或启用任何脚本。只有用户
明确说“保存 / 发布 / 部署 / 更新到 DEV”后才可生成写入计划；计划必须展示给用户，收到后续
明确确认后才可携带一次性 `confirmation_token` 执行。

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
| `SCRIPT_PLATFORM_ENV_DIR` | 否 | 自动发现 `.env` 所在目录 | 配置根目录；建议 MCP 宿主传入绝对路径，所有相对路径都以此目录解析 |
| `SCRIPT_PLATFORM_TOKEN_FILE` | 否 | `.auth/token.json` | 自动管理的 Token 缓存；原子写入且权限为 0600 |
| `SCRIPT_PLATFORM_TOKEN_FILES` | 否 | 空 | 可选兼容 Token 文件列表；支持 `token` / `access_token`，文件须为 0600 |
| `SCRIPT_PLATFORM_SSO_USERNAME` | 否 | 空 | SSO 用户名；与密码一起配置即可跨 macOS 使用 |
| `SCRIPT_PLATFORM_SSO_PASSWORD` | 否 | 空 | SSO 密码；优先级最高，建议仅写入本地 `.env`，不要提交 Git |
| `SCRIPT_PLATFORM_ACCOUNT_FILE` | 否 | `.auth/account.json` | Keychain 模式下保存非秘密用户名 |
| `SCRIPT_PLATFORM_SSO_CREDENTIAL_FILE` | 否 | `.auth/credentials.json` | 无人值守推荐的 0600 私有账号文件，不提交 Git |
| `SCRIPT_PLATFORM_KEYCHAIN_SERVICE` | 否 | `zhenyun-script-platform-sso` | 可选 macOS Keychain service |
| `SCRIPT_PLATFORM_TOKEN_URL` / `CLIENT_ID` | 否 | DEV SSO / `srm-dev` | refresh token 续期参数 |
| `SCRIPT_PLATFORM_AUTHORIZE_URL` / `REDIRECT_URI` | 否 | DEV SSO / DEV 业务站 | 浏览器 SSO 参数 |
| `SCRIPT_PLATFORM_CHROME_PATH` / `CHROME_PROFILE_DIR` | 否 | 系统 Chrome / `.auth/chrome-profile` | 自动登录浏览器与隔离 Profile |
| `SCRIPT_PLATFORM_TOKEN_REFRESH_SKEW_SECONDS` | 否 | `120` | 已知有效期 Token 的提前刷新窗口 |
| `SCRIPT_PLATFORM_CONFIRM_TTL_SECONDS` | 否 | `600` | 写入计划确认令牌有效期；令牌只能使用一次 |
| `SCRIPT_PLATFORM_AUTHORIZATION_SCHEME` | 否 | `bearer` | `bearer` 或 `raw`，适配两种已观察到的网关 Authorization 形态 |
| `SCRIPT_PLATFORM_COOKIE` | 否 | 空 | 仅在目标环境确实需要时配置 |
| `SCRIPT_PLATFORM_MENU_ID` | 否 | 空 | 可选 `H-Menu-Id` |
| `SCRIPT_PLATFORM_TIMEOUT` | 否 | `30` | HTTP 超时秒数 |
| `SCRIPT_PLATFORM_VERIFY_SSL` | 否 | `true` | 是否校验证书 |
| `SCRIPT_PLATFORM_DEFAULT_PAGE_SIZE` / `MAX_PAGE_SIZE` | 否 | `20` / `100` | 通用资源查询分页边界 |
| `SCRIPT_PLATFORM_TEXT_PREVIEW_CHARS` | 否 | `500` | 列表中日志与长文本预览上限 |
| `SCRIPT_PLATFORM_CREATE_VERIFY_ATTEMPTS` / `CREATE_VERIFY_DELAY_SECONDS` | 否 | `4` / `0.5` | 创建后回读验证的最大尝试次数与间隔秒数 |

不再使用写开关、Host 白名单或租户白名单。每个平台写工具固定为两阶段协议：第一次只返回目标、
版本、变更摘要、请求摘要和短效签名，不发平台写请求；Agent 必须停止并请用户确认。第二次
参数必须完全一致且携带签名，确认令牌默认十分钟有效、只能使用一次，签名过期、复用或参数漂移
都会被拒绝。
确认令牌的签名密钥和已使用记录保存在当前 MCP 进程内；MCP 重启后，未执行的计划需要重新生成。

MCP 宿主的当前工作目录不作为配置根目录。若从 IDE、插件缓存或任意目录启动，建议在宿主配置中
显式传入 `SCRIPT_PLATFORM_ENV_DIR`，避免 Token、凭据和 Chrome Profile 写入错误目录。

Token 文件只保存于本机，不会被插件同步。MCP 会复用未过期 Token；有 refresh token 时优先
续期；JWT/显式有效期过期时才刷新；无过期信息的 opaque Token 持续复用，只有平台真实返回
401 或 Token 失效包络后才自动刷新或浏览器登录一次并重试。浏览器登录会同时尝试保存
`access_token`、`refresh_token`、`expires_in` 和 `refresh_expires_in`，以支持后续静默刷新；
任何工具响应都不会返回 Token 内容。

认证凭据按以下顺序读取：完整的 `SCRIPT_PLATFORM_SSO_USERNAME` +
`SCRIPT_PLATFORM_SSO_PASSWORD`（通常来自 `.env`）→ 0600 私有凭据文件 → macOS Keychain。
因此非 macOS 用户只需在 `.env` 配置账号密码即可；配置完整时不会尝试读取 Keychain。

跨平台使用时，在本地 `.env` 配置：

```dotenv
SCRIPT_PLATFORM_SSO_USERNAME=<你的 SSO 账号>
SCRIPT_PLATFORM_SSO_PASSWORD=<你的 SSO 密码>
```

`.env` 已被 Git 忽略，但仍应限制文件权限并避免分享或提交真实密码。

如果不希望把密码放在 `.env`，首次配置也可以运行（密码通过终端隐藏输入，写入 `.gitignore` 下的 0600 文件）：

```bash
uv run zhenyun-script-platform-auth store-password --username '<你的 SSO 账号>'
uv run zhenyun-script-platform-auth login --force
uv run zhenyun-script-platform-auth status
```

如桌面环境允许 Keychain 解锁，可加 `--storage keychain`。已有私有账号/Token 文件可分别用
`import-credentials --from-file ...` 和 `import-token --from-file ...` 迁移。

## 通用资源写入边界

- `resource_type` 是固定枚举；工具不接受任意 URL、路径或表名。
- 只有经验证的 10 张 Rel-Table 可走通用 CRUD；`adapter_event` 等只读资源不会因被登记而获得写权限。
- `platform_resource_save/delete` 和 `platform_table_action` 必须携带最新
  `objectVersionNumber`，版本漂移立即拒绝。
- `platform_resource_create` 对 `tenantNum` 资源自动补 `tenantId: 0`；`scheduler` 不补该默认值，
  只接受数字 `tenantId`。
- `independent_script_create` 以及独立脚本的通用创建都要求调用方提供平台必填的
  `permission`、`module`，并会在首阶段确认前校验编码/需求描述格式，统一编码
  `content`/`contentInput`；`platform_definition_get` 会返回已知平台校验提示。
- 独立脚本的 `quickType` 可能连带创建或引用 `api_publish`、`queue_consumer`、`scheduler`；
  删除前会扫描引用，创建计划和执行结果也会提示检查关系。
- 常量 `value` 等秘密字段不会返回，也禁止经通用 create/save 穿过模型上下文。
- `scheduler` 使用数字 `tenantId`；其它多数资源使用租户编码 `tenantNum`。
- `platform_relations_get` 的 `tenant` 按多数资源解释为 `tenantNum`；扫描 scheduler 时如需
  租户级准确结果，应额外传数字 `scheduler_tenant_id`。只传租户编码时工具会做无租户过滤扫描并明确警告，
  不再把 `scanned: 0` 当作无引用。
- DELETE 会携带完整记录且成功可能返回 204 空体；HTTP 200 下的 XML/JSON 业务失败也会被识别。

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
6. 仅当校验成功且（原状态启用或显式 `enable=true`）时重新启用，再 GET 验证最终状态。

`enable` 默认为 false，保持“只恢复原状态”的语义；仅当用户明确要求脚本最终处于启用状态时
传 `enable=true`，此时原本停用的 Adapter 在保存校验成功后也会被启用。

平台约束：列表接口 `/adaptor-task-headers` 返回的是前端视图，不包含编辑态字段 `_status`，
而 `AdaptorTaskHeaderServiceImpl.saveAdaptor` 会对每条 Line 解引用 `_status`，缺失时报
`NullPointerException`（HTTP 200 + `unknownException`）。保存 payload 因此必须为每条已存在
Line 补 `_status="update"`；`adapter_deploy` 已自动补齐，直接手写 payload 时需自行处理。

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
  -> adapter_deploy（第一次仅生成签名计划）
  -> 向用户展示计划并结束当前轮
  -> 用户后续明确确认
  -> adapter_deploy（相同参数 + confirmation_token，才执行）
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
        "SCRIPT_PLATFORM_ENV_DIR": "/ABSOLUTE/PATH/zhenyun-script-platform-mcp"
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
