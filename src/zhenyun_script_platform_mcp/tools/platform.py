from __future__ import annotations

from typing import Any

from ..services.platform import PlatformResourceService


def search_resources(service: PlatformResourceService, **kwargs: Any) -> dict[str, Any]:
    return service.search(**kwargs)


def get_resource(service: PlatformResourceService, **kwargs: Any) -> dict[str, Any]:
    return service.get(**kwargs)


def get_definition(service: PlatformResourceService, **kwargs: Any) -> dict[str, Any]:
    return service.definition(**kwargs)


def get_relations(service: PlatformResourceService, **kwargs: Any) -> dict[str, Any]:
    return service.relations(**kwargs)


def list_api_points(service: PlatformResourceService, **kwargs: Any) -> dict[str, Any]:
    return service.api_points(**kwargs)


def create_resource(service: PlatformResourceService, **kwargs: Any) -> dict[str, Any]:
    return service.create(**kwargs)


def save_resource(service: PlatformResourceService, **kwargs: Any) -> dict[str, Any]:
    return service.save(**kwargs)


def delete_resource(service: PlatformResourceService, **kwargs: Any) -> dict[str, Any]:
    return service.delete(**kwargs)


def execute_table_action(service: PlatformResourceService, **kwargs: Any) -> dict[str, Any]:
    return service.execute_action(**kwargs)
