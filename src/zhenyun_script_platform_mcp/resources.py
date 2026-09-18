"""Closed Script Platform resource and action contracts.

The paths and field mappings in this module were verified by the reference WebOps
implementation on DEV.  Keeping them as closed enums prevents a generic MCP call
from becoming an arbitrary URL or table proxy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ResourceKind = Literal["query", "rel-table", "script-log"]
ResourceType = Literal[
    "adapter_task",
    "independent_script",
    "adapter_inventory",
    "queue_consumer",
    "api_publish",
    "api_rewrite",
    "data_import",
    "scheduler",
    "constant",
    "outbound_whitelist",
    "code_block",
    "query_block",
    "script_log",
    "adapter_event",
]


@dataclass(frozen=True, slots=True)
class ResourceDefinition:
    kind: ResourceKind
    platform_id: str
    label: str
    code_field: str
    list_path: str
    tenant_param: str
    tenant_field: str
    writable: bool = False
    definition_readable: bool = True
    mask_fields: tuple[str, ...] = ()
    truncate_fields: tuple[str, ...] = ()
    validation_notes: tuple[str, ...] = ()

    @property
    def record_path(self) -> str:
        if not self.writable or self.kind != "rel-table":
            raise ValueError(
                f"Resource {self.platform_id!r} is not writable through Rel-Table CRUD"
            )
        return f"/sada/v1/rel-table-records/{self.platform_id}"


RESOURCES: dict[str, ResourceDefinition] = {
    "adapter_task": ResourceDefinition(
        "query",
        "adaptor-task-headers",
        "适配器任务",
        "taskCode",
        "/sada/v1/adaptor-task-headers",
        "applyTenantNum",
        "applyTenantNum",
        definition_readable=False,
    ),
    "independent_script": ResourceDefinition(
        "rel-table",
        "marmot_script_library",
        "独立脚本",
        "code",
        "/sada/v1/rel-table-records/marmot_script_library/page",
        "tenantNum",
        "tenantNum",
        writable=True,
        truncate_fields=("content", "contentInput"),
        validation_notes=(
            "code must contain only uppercase letters, digits, and underscores",
            "description must start with a demand code such as cdp-00000",
            "permission and module are required on create",
            "quickType may create a related api_publish, queue_consumer, or scheduler record",
        ),
    ),
    "adapter_inventory": ResourceDefinition(
        "query",
        "adaptor-script/search",
        "适配器脚本全览",
        "taskCode",
        "/sada/v1/adaptor-script/search",
        "applyTenantNum",
        "applyTenantNum",
        definition_readable=False,
    ),
    "queue_consumer": ResourceDefinition(
        "rel-table",
        "marmot_queue_consumer",
        "Topic 消费端",
        "topic",
        "/sada/v1/rel-table-records/marmot_queue_consumer/page",
        "tenantNum",
        "tenantNum",
        writable=True,
    ),
    "api_publish": ResourceDefinition(
        "rel-table",
        "marmot_api_publish",
        "API 发布",
        "code",
        "/sada/v1/rel-table-records/marmot_api_publish/page",
        "tenantNum",
        "tenantNum",
        writable=True,
    ),
    "api_rewrite": ResourceDefinition(
        "rel-table",
        "marmot_api_rewrite",
        "API 改写/挂载",
        "apiCode",
        "/sada/v1/rel-table-records/marmot_api_rewrite/page",
        "tenantNum",
        "tenantNum",
        writable=True,
    ),
    "data_import": ResourceDefinition(
        "rel-table",
        "marmot_data_import",
        "功能数据导入配置",
        "templateCode",
        "/sada/v1/rel-table-records/marmot_data_import/page",
        "tenantNum",
        "tenantNum",
        writable=True,
    ),
    "scheduler": ResourceDefinition(
        "rel-table",
        "marmot_scheduler",
        "调度",
        "jobCode",
        "/sada/v1/rel-table-records/marmot_scheduler/page",
        "tenantId",
        "tenantId",
        writable=True,
    ),
    "constant": ResourceDefinition(
        "rel-table",
        "sada_adaptor_constants",
        "常量设定",
        "constantCode",
        "/sada/v1/rel-table-records/sada_adaptor_constants/page",
        "tenantNum",
        "tenantNum",
        writable=True,
        mask_fields=("value",),
    ),
    "outbound_whitelist": ResourceDefinition(
        "rel-table",
        "marmot_script_outbound_whitelist",
        "OutBound 白名单",
        "host",
        "/sada/v1/rel-table-records/marmot_script_outbound_whitelist/page",
        "tenantNum",
        "tenantNum",
        writable=True,
    ),
    "code_block": ResourceDefinition(
        "rel-table",
        "sada_adaptor_code_block",
        "CodeBlock",
        "blockCode",
        "/sada/v1/rel-table-records/sada_adaptor_code_block/page",
        "tenantNum",
        "tenantNum",
        writable=True,
        truncate_fields=("content",),
    ),
    "query_block": ResourceDefinition(
        "rel-table",
        "sada_adaptor_query_block",
        "QueryBlock",
        "queryBlockCode",
        "/sada/v1/rel-table-records/sada_adaptor_query_block/page",
        "tenantNum",
        "tenantNum",
        writable=True,
        truncate_fields=("sqlContent", "countSql"),
    ),
    "script_log": ResourceDefinition(
        "script-log",
        "script-log-records",
        "脚本日志",
        "taskCode",
        "/sada/v1/script-log-records/query",
        "tenantNum",
        "tenantNum",
        definition_readable=False,
        truncate_fields=("content",),
    ),
    "adapter_event": ResourceDefinition(
        "rel-table",
        "adaptor_static_code",
        "适配器事件编码注册表",
        "taskCode",
        "/spfm/v1/rel-table-records/adaptor_static_code/page",
        "tenantId",
        "tenantId",
    ),
}

RESOURCE_TYPES = tuple(RESOURCES)


@dataclass(frozen=True, slots=True)
class TableAction:
    id: int
    name: str
    effect: Literal["display", "side_effect"]
    backend: str | None = None
    note: str | None = None


TABLE_ACTIONS: dict[str, tuple[TableAction, ...]] = {
    "marmot_api_publish": (
        TableAction(25, "CURL（仅供调试使用）", "display"),
        TableAction(123, "唯一编码", "display"),
    ),
    "marmot_scheduler": (
        TableAction(167, "执行", "side_effect", "POST /v1/marmot-job-info/trigger"),
        TableAction(168, "暂停", "side_effect", "POST /v1/marmot-job-info/pause"),
        TableAction(169, "恢复", "side_effect", "POST /v1/marmot-job-info/resume"),
        TableAction(170, "终止", "side_effect", "POST /v1/marmot-job-info/stop"),
        TableAction(877, "注意事项", "display"),
    ),
    "marmot_script_library": (
        TableAction(129, "唯一编码", "display"),
        TableAction(694, "注意事项", "display"),
    ),
    "marmot_script_outbound_whitelist": (
        TableAction(105, "连通性测试", "side_effect", note="会真实发起出站连接"),
    ),
    "sada_adaptor_code_block": (TableAction(83, "唯一编码", "display"),),
    "sada_adaptor_constants": (TableAction(231, "常量说明", "display"),),
    "sada_adaptor_query_block": (
        TableAction(81, "唯一编码", "display"),
        TableAction(321, "测试重复", "display"),
        TableAction(656, "注意事项", "display"),
    ),
}


def resource_definition(resource_type: str) -> ResourceDefinition:
    try:
        return RESOURCES[resource_type]
    except KeyError as exc:
        raise ValueError(
            f"Unknown resource_type {resource_type!r}; expected one of {', '.join(RESOURCE_TYPES)}"
        ) from exc


def table_action(platform_id: str, action_id: int) -> TableAction:
    actions = TABLE_ACTIONS.get(platform_id, ())
    for action in actions:
        if action.id == action_id:
            return action
    available = ", ".join(f"{item.id}:{item.name}" for item in actions) or "none"
    raise ValueError(
        f"action_id {action_id} is not registered for {platform_id}; available actions: {available}"
    )
