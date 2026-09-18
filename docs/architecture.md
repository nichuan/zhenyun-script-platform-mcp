# Architecture

## 1. 系统边界

```text
                         Codex / CodeBuddy
                         (workflow owner)
                         /              \
                        v                v
        zhenyun-script-platform-mcp   zhenyun-pangu-mcp
        get/debug/save/deploy         logs / DB / knowledge
                        |                |
                        v                v
              DEV Script Platform    Loki/SLS/DB/etc.
                        |
                        v
                Real GraalJS Runtime
             STD / BIZ / @M / services
```

两个 MCP 独立部署、独立配置且互不 import。Script Platform MCP 不查询日志/数据库；Pangu 不
参与脚本平台状态切换。跨系统步骤由 Agent 组织。

## 2. 分层

```text
FastMCP tools (script lifecycle + verified platform resources)
        |
tools/* thin handlers
        |
        services/{independent,adapter,debug,fixture,platform}
        |
models + codec + sanitizer + exceptions
        |
ScriptPlatformClient + AuthProvider
        |
HTTP Script Platform API
```

Tool 不直接发 HTTP。HTTP Client 统一管理 Base URL、认证、超时、可选 Cookie/Menu Header、
HTTP/业务错误转换。Service 持有完整平台对象，公共 Model 则隐藏 `_token` 和未声明元数据。

## 3. Independent Runtime

```text
POST marmot_script_library/page
        -> exact tenantNum + code match
        -> decode content/contentInput
        -> public source + hash + fixture status
```

Debug 直接编码调用方给出的当前源码并调用 `/script-debug/run`，与数据库中的源码版本无关。

保存采用 read-modify-write：重新查询最新完整 Record，检查 `expected_version`，深复制完整对象
并只替换 `content`，PUT 后再次查询、解码并逐字比较。这样保留平台内部 `_token` 和将来新增
字段，并避免覆盖并发修改。

## 4. Adapter Runtime

Adapter 用 `(taskCode, runningService, applyTenantNum)` 精确定位。Header 与 Lines 分别保留自己的
`objectVersionNumber`。所有 Line 都解码返回；多 Line 未指定 `line_id` 时返回
`AMBIGUOUS_LINE`，不会隐式选第一条。

Adapter Debug 只读取当前 Header 取得 `scriptVersion` 和验证 Line，然后提交：

```text
POST /sada/v1/script-debug/run
  ?debugTenantNum=<tenant>
  &scriptVersion=<current header scriptVersion>
body = {
  script: UTF16BE_BASE64(current_editor_source),
  rawInputJsonStr: JSON_STRING
}
```

它绝不调用 toggle/save。

## 5. Remote Debug 响应

Debug Service 保留平台原始结构的脱敏副本，同时规范化：

1. 若第一层 `result` 是 JSON 字符串，解析一次。
2. 若解析结果是对象且 `body` 仍是 JSON 字符串，再解析一次。
3. 任一层不是 JSON 时保留原始字符串。
4. `outPutLog`、`executionInfo`、`queryBlockSql` 分别映射到稳定字段。

这让 Agent 能使用结构化结果，同时不因平台返回非 JSON 文本而丢失信息。

## 6. Fixture Flow

```text
explicit raw_input (highest priority)
        |
saved encoded fixture -> decode -> JSON -> classify
        |
Pangu log text -> adapter_extract_input -> balanced parser
        |
none / placeholder / invalid -> NO usable fixture
```

Balanced Parser 是逐字符状态机，跟踪 `{}` / `[]` 栈、JSON 字符串和反斜杠转义。它能忽略
字符串内的括号，避免贪婪正则截断或串联多段 JSON。

## 7. Deployment State Machine

```text
GET A (capture original_enabled + version check)
  |
  +-- disabled --------------------------------------+
  |                                                  |
  +-- enabled -> TOGGLE false -> GET B -> assert off |
                                                     v
                                   build full payload from latest GET
                                   replace one scriptContent only
                                                     |
                                                   SAVE
                                                     |
                                                   GET C
                                                     |
                                source mismatch -----+---- source exact
                                keep disabled              |
                                manual attention           +-- originally off
                                                          |   finish off
                                                          |
                                                          +-- originally on
                                                              TOGGLE true
                                                              GET D / assert on
```

停用后强制重新 GET，因为 toggle 可能修改版本、时间、`_token` 或其他隐藏字段。

## 8. Error Recovery

- Disable 成功、Save 失败：尽力 Toggle true 并 GET 确认，返回 `enabled_restored`。
- Save 成功、回读源码不一致：保持 disabled，抛出 `SAVE_VERIFICATION_FAILED`。
- Save/校验成功、Re-enable 失败：不把它伪装成保存失败；返回 `saved=true`、
  `RE_ENABLE_FAILED`、`requires_manual_attention=true`。
- 预期版本与实际版本不一致：在任何状态变化前返回 `VERSION_CONFLICT`。
- 401/403、404、409、5xx 和 HTTP 200 业务失败都转换成稳定领域错误，不把 traceback 交给
  Agent。

## 9. Security Boundary

- 所有 10 个持久化/动作工具都执行两阶段协议：第一次只产生绑定工具名与完整参数的短效签名
  计划，不访问写接口；第二次必须携带用户后续明确确认的同一计划。签名过期、复用或参数变化
  均拒绝。
- 不再使用全局写开关、Host 白名单或租户白名单；人工确认、封闭资源枚举、乐观锁和回读校验
  是互相独立的保护层。
- Authorization/Cookie 只来自权限为 0600 的本地缓存、本机认证配置或本地 `.env` 凭据，不进入
  源码、测试 Fixture 或文档。有效 Token 持续复用；已知过期才刷新，opaque Token 仅在平台拒绝
  后登录并重试一次。
- HTTP 日志不记录源码、业务 Input 或凭据。
- `authorization`、Token、Cookie、Password 和平台 `_token` 在 MCP 输出边界递归隐藏；完整
  `_token` 仅留在内存中的保存 payload。
- 不提供裸 `adapter_disable`、`adapter_enable`、`adapter_save_raw` 工具，避免 Agent 组合出不
  完整状态机。
- 通用资源和表动作均由封闭枚举控制；新增只读资源不会自动扩大写入表集合。
- 通用更新、删除和表动作强制使用已读取版本，秘密字段不允许经模型上下文写入。
- 不本地模拟 GraalJS、STD、BIZ 或 `@M`，真实语义只由 DEV Runtime 验证。
