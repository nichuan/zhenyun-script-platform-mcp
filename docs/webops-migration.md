# WebOps TypeScript 能力迁移说明

参考源：`/Users/chuanni/Downloads/webops-mcp`，分析日期 2026-09-18。

## 迁移结论

参考实现的优势是平台覆盖面和真实 DEV 契约证据；现有 Python MCP 的优势是脚本领域模型、
版本安全、回读校验、错误码和部署恢复状态机。本次保留 Python 架构，把参考实现的增量能力
收敛进同一个服务，而不是逐文件翻译。

| 参考能力 | 当前落点 | 处理 |
| --- | --- | --- |
| context / capabilities | `platform_context_get` / `platform_capabilities_list` | 已迁移；认证信息只返回元数据 |
| 14 类资源检索与详情 | `platform_resource_search/get` | 已迁移；逐资源查询字段白名单、组合筛选、分页上限、租户回读、字段脱敏 |
| Rel-Table 字段定义 | `platform_definition_get` | 已迁移；本地解析 `mappingInfo/mappingJson`，不回传大 JSON 原文；DEV 实测 11 类可用，3 类无表规则/权限并 fail-closed |
| 脚本引用关系 | `platform_relations_get` | 已迁移；明确标注有界扫描，未命中不等于不存在 |
| API 挂载点 | `platform_api_point_list` | 已迁移；保留 `classBeanName -> beanName` 映射说明 |
| 10 表通用 CRUD | `platform_resource_create/save/delete` | 已迁移；更新/删除强制版本，秘密字段禁止穿过模型 |
| 15 个表级动作快照 | `platform_table_action` | 已迁移；表/actionId 硬绑定，数组请求体，副作用分级 |
| Adapter 新建/改元数据/启停/删除 | `adapter_create/update/toggle/delete` | 已迁移；删除启用态 Adapter 会拒绝 |
| Adapter 源码保存 | `adapter_deploy` | 已有且更强：停用、重新读取、完整保存、回读校验、恢复启用 |
| Independent Script 读/Debug/Save | 既有三个高层工具 | 已有且更强：明文模型、Fixture 分类和版本校验 |
| 脚本 Debug | 既有 Debug 工具 | 已有；不重复增加 `marmot_script_test` |
| 浏览器 SSO / `token.json` / `keycloak.json` | `AuthProvider` + `BrowserAuthenticator` + `zhenyun-script-platform-auth` | 已迁移并增强；Token 复用、有效期/refresh、真实失效后浏览器登录、0600 原子缓存与一次重试 |
| XML/JSON 伪 200、DELETE 204 | `ScriptPlatformClient` | 已迁移并统一为领域错误 |

## 有意不照搬

- **真实 Token 内容**：不复制、不提交、不进入测试或文档。只提供文件引用与热加载机制。
- **凭据优先级**：支持将账号密码配置在本地 `.env`，优先级高于 0600 私有凭据文件和 macOS
  Keychain，便于非 macOS 用户使用；`.env` 不提交 Git。Token 使用独立 0600 缓存。
- **可被同轮自动消费的 plan/approve**：改造成服务端 HMAC 两阶段协议。第一次只返回绑定工具名和
  完整参数的短效单次签名；Skill 强制展示计划并结束当前轮，必须等待用户后续确认。签名本身
  不能替代人的工作流约束，因此同时保留封闭资源、期望版本和回读验证。
- **任意 URL/表/原始请求工具**：不迁移，防止资源登记或提示词绕过扩大写面。
- **HZERO 通用导入执行和 API 测试**：参考实现已确认当前账号无权限且不是 Marmot 业务接口，
  能力清单会明确标记 blocked，不提供假工具。

## 已吸收的修正

- 平台可能返回 HTTP 200 + XML 错误或 HTTP 200 + JSON `{failed:true}` / error envelope，
  不能只看状态码。
- Adapter 保存每条 Line 都需要 `_status`；新建 Header 必须携带初始 Line。
- Rel-Table DELETE 带完整请求体并可能成功返回 204 空体。
- 调度租户字段是数字 `tenantId`，不能当 `tenantNum` 使用。
- Adapter `toggle-cache` 虽然是 GET，但语义是写；工具注解和授权按副作用而不是 HTTP 方法判断。
- 参考 SSO 的业务 Token 不一定可解码出有效期；这类 opaque Token 不做每请求探测，只有真实
  401/失效包络时才重新认证并把原请求重试一次。
- 14 类资源查询全部可用，但 Adapter 任务、Adapter 全览和脚本日志没有可用字段定义；能力清单会
  把这 3 类标记为 `definition=false`，不把 403 或“无表规则”伪装成可支持能力。
- 通用检索不再把所有 Rel-Table 的 `text` 强制映射成 `description`；每类资源登记自己的
  `text_param` 和 `query_fields`，额外组合条件必须经过白名单。调度查询可把租户编码通过固定
  `HPFM.TENANT_PAGING` LOV 精确转换为数字 `tenantId`。
- 源码正文使用显式字段白名单绕过关键字级脱敏，保证 `adapter_get.source` 与 `source_hash`
  字节一致；Fixture 和普通元数据继续脱敏。保存、部署和远程调试会预先拒绝已知脱敏占位符。
