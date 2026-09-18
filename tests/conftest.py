from copy import deepcopy

import pytest

from zhenyun_script_platform_mcp.codec import encode_platform_text
from zhenyun_script_platform_mcp.config import Settings


class FakeAdapterClient:
    def __init__(self, *, enabled=True, line_count=1):
        self.events = []
        self.save_fails = False
        self.enable_fails = False
        self.ignore_save = False
        self.last_debug = None
        self.present = True
        lines = []
        for index in range(line_count):
            line_id = 101 + index
            lines.append(
                {
                    "id": line_id,
                    "headerId": 50,
                    "objectVersionNumber": 20 + index,
                    "scriptContent": encode_platform_text(f"return line{index + 1};"),
                    "inputContent": encode_platform_text('{"ANYTHING":"string"}'),
                    "outputEntityCode": "java.util.Map",
                    "scriptType": "JS",
                    "priority": index + 1,
                    "taskCode": "TASK",
                    "_token": f"line-token-{index}",
                }
            )
        self.header = {
            "id": 50,
            "taskCode": "TASK",
            "description": "test adapter",
            "inputEntityCode": "java.util.Map",
            "applyTenantNum": "SRM-DEMO",
            "runningService": "srm-source",
            "scriptVersion": 3,
            "enabledFlag": enabled,
            "objectVersionNumber": 15,
            "_token": "header-token",
            "adaptorTaskLines": lines,
        }

    def get(self, path, *, params=None):
        if path.endswith("/toggle-cache"):
            enabled = params["enabledFlag"] == "true"
            self.events.append(f"TOGGLE {str(enabled).lower()}")
            if enabled and self.enable_fails:
                raise RuntimeError("enable failed")
            self.header["enabledFlag"] = enabled
            self.header["objectVersionNumber"] += 1
            return {"success": True}
        self.events.append("GET")
        return {"content": [deepcopy(self.header)] if self.present else []}

    def post(self, path, *, json=None, params=None):
        if path.endswith("/script-debug/run"):
            self.events.append("DEBUG")
            self.last_debug = (params, deepcopy(json))
            return {"result": '{"ok":true}', "outPutLog": "done"}
        self.events.append("SAVE")
        if self.save_fails:
            raise RuntimeError("save failed")
        if not self.ignore_save:
            self.header = deepcopy(json)
            creating = self.header.get("id") is None
            if creating:
                self.header["id"] = 50
                self.header["objectVersionNumber"] = 1
                self.header["enabledFlag"] = False
                self.header["scriptVersion"] = self.header.get("scriptVersion", 3)
            else:
                self.header["objectVersionNumber"] += 1
            for line in self.header["adaptorTaskLines"]:
                if line.get("id") is None:
                    line["id"] = 101
                    line["headerId"] = self.header["id"]
                    line["objectVersionNumber"] = 1
                    line["scriptContent"] = line.get(
                        "scriptContent", encode_platform_text("return input;")
                    )
                    line["inputContent"] = line.get(
                        "inputContent", encode_platform_text('{"ANYTHING":"string"}')
                    )
                else:
                    line["objectVersionNumber"] += 1
            self.present = True
        return {"success": True}

    def delete(self, path, *, json=None, params=None):
        self.events.append("DELETE")
        self.present = False


@pytest.fixture
def write_settings():
    return Settings(base_url="https://gateway.dev.example.com")


@pytest.fixture
def adapter_client():
    return FakeAdapterClient()
