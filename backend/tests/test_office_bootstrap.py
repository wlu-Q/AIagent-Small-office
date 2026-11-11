# 本文件属于 Office 初始化测试模块：只验证公司初始化、默认模型和 PM 启动边界。
# 不负责 DeerFlow Agent Loop、消息路由、员工能力匹配或前端视觉交互。
# 关联：office/bootstrap.py、gateway/routers/office.py 与 employees.py 共同提供办公室进入前的准备流程。
from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
from openai import APIConnectionError, APITimeoutError, AuthenticationError, InternalServerError
import pytest
import yaml

from app.gateway.routers.office import router
from deerflow.config.app_config import AppConfig
from deerflow.config.paths import Paths
from deerflow.office.projects import OfficeProjectService


def _project_service(tmp_path):
    return OfficeProjectService(paths=Paths(tmp_path / '.deer-flow'), user_id='default')


class _BootstrapService:
    def get_status(self) -> dict[str, object]:
        return {
            "initialized": False,
            "companyName": None,
            "defaultModel": None,
            "thinkingLevel": "high",
            "baseUrl": None,
            "apiKeyConfigured": False,
        }

    async def test_connection(self, payload: dict[str, object]) -> dict[str, str]:
        assert payload["modelName"] == "mimo-v2.5"
        return {
            "name": "office-default",
            "model": "mimo-v2.5",
            "displayName": "mimo-v2.5",
        }

    async def initialize(self, payload: dict[str, object]) -> dict[str, object]:
        assert payload["companyName"] == "星河创意工作室"
        return {
            "initialized": True,
            "companyName": "星河创意工作室",
            "defaultModel": {
                "name": "office-default",
                "model": "mimo-v2.5",
                "displayName": "mimo-v2.5",
            },
        }

    async def update_settings(self, payload: dict[str, object]) -> dict[str, object]:
        assert payload["companyName"] == "新办公室"
        return {
            "initialized": True,
            "companyName": "新办公室",
            "defaultModel": {
                "name": "office-default",
                "model": "mimo-v2.5",
                "displayName": "mimo-v2.5",
            },
            "baseUrl": "https://example.com/v1",
            "apiKeyConfigured": True,
        }


def test_bootstrap_status_exposes_uninitialized_office() -> None:
    app = FastAPI()
    app.include_router(router)
    app.state.office_bootstrap_service = _BootstrapService()

    response = TestClient(app).get("/api/office/bootstrap")

    assert response.status_code == 200
    assert response.json() == {
        "initialized": False,
        "companyName": None,
        "defaultModel": None,
            "thinkingLevel": "high",
        "baseUrl": None,
        "apiKeyConfigured": False,
    }


def test_bootstrap_routes_test_then_initialize_office() -> None:
    app = FastAPI()
    app.include_router(router)
    app.state.office_bootstrap_service = _BootstrapService()
    client = TestClient(app)
    payload = {
        "companyName": "星河创意工作室",
        "modelName": "mimo-v2.5",
        "baseUrl": "https://token-plan-cn.xiaomimimo.com/v1",
        "apiKey": "test-secret",
    }

    tested = client.post("/api/office/bootstrap/test-model", json=payload)
    initialized = client.post("/api/office/bootstrap", json=payload)

    assert tested.status_code == 200
    assert tested.json()["model"] == "mimo-v2.5"
    assert initialized.status_code == 200
    assert initialized.json()["initialized"] is True


def test_settings_route_updates_existing_office_configuration() -> None:
    app = FastAPI()
    app.include_router(router)
    app.state.office_bootstrap_service = _BootstrapService()

    response = TestClient(app).patch(
        "/api/office/settings",
        json={
            "companyName": "新办公室",
            "modelName": "mimo-v2.5",
            "baseUrl": "https://example.com/v1",
            "apiKey": "new-secret",
        },
    )

    assert response.status_code == 200
    assert response.json()["companyName"] == "新办公室"


@pytest.mark.parametrize("route", ["/api/office/bootstrap/test-model", "/api/office/bootstrap"])
@pytest.mark.parametrize("failure,expected_status,code", [
    ("auth", 401, "errors.provider_auth"),
    ("timeout", 504, "errors.provider_timeout"),
    ("connection", 502, "errors.provider_verification"),
    ("provider", 500, "errors.provider_http"),
])
def test_model_connection_errors_return_safe_json(route, failure, expected_status, code) -> None:
    request = httpx.Request("POST", "https://example.com/v1/chat/completions")
    if failure == "auth":
        error = AuthenticationError("Invalid API Key: test-secret", response=httpx.Response(401, request=request), body=None)
    elif failure == "provider":
        error = InternalServerError("Internal Server Error: test-secret", response=httpx.Response(500, request=request), body=None)
    elif failure == "timeout":
        error = APITimeoutError(request=request)
    else:
        error = APIConnectionError(request=request)

    class FailingBootstrap(_BootstrapService):
        async def test_connection(self, payload):
            raise error

        async def initialize(self, payload):
            raise error

    app = FastAPI()
    app.include_router(router)
    app.state.office_bootstrap_service = FailingBootstrap()
    response = TestClient(app, raise_server_exceptions=False).post(route, json={})

    assert response.status_code == expected_status
    assert response.headers["content-type"] == "application/json"
    assert response.json()["detail"]["code"] == code
    assert "test-secret" not in response.text


class _EmployeeService:
    def __init__(self) -> None:
        self.default_model_profile_ids: list[str | None] = []

    def set_default_model_profile_id(self, model_profile_id: str | None) -> None:
        self.default_model_profile_ids.append(model_profile_id)

    async def ensure_builtin_pm(self, *, default_model_profile_id: str | None) -> dict[str, str]:
        self.default_model_profile_ids.append(default_model_profile_id)
        return {"id": "pm"}


@pytest.mark.asyncio
async def test_initialize_persists_company_default_model_and_binds_pm(tmp_path) -> None:
    from deerflow.office.bootstrap import OfficeBootstrapService

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "sandbox:\n  use: deerflow.sandbox.local:LocalSandboxProvider\nmodels: []\n",
        encoding="utf-8",
    )
    employee_service = _EmployeeService()
    projects = _project_service(tmp_path)
    workspace = tmp_path / '中文 工作区'
    workspace.mkdir()
    projects.bind(workspace)
    tested_model_names: list[str] = []

    async def test_model(config: AppConfig, model_name: str) -> None:
        tested_model_names.append(config.get_model_config(model_name).model)

    service = OfficeBootstrapService(
        paths=Paths(tmp_path / ".deer-flow"),
        user_id="default",
        config_path=config_path,
        employee_service=employee_service,
        project_service=projects,
        test_model=test_model,
    )

    result = await service.initialize(
        {
            "companyName": "星河创意工作室",
            "modelName": "mimo-v2.5",
            "baseUrl": "https://token-plan-cn.xiaomimimo.com/v1",
            "apiKey": "test-secret",
        }
    )

    assert tested_model_names == ["mimo-v2.5"]
    assert _project_service(tmp_path).get_binding().path == workspace.resolve()
    assert employee_service.default_model_profile_ids == ["office-default", "office-default"]
    assert result == {
        "initialized": True,
        "companyName": "星河创意工作室",
        "defaultModel": {
            "name": "office-default",
            "model": "mimo-v2.5",
            "displayName": "mimo-v2.5",
        },
            "thinkingLevel": "high",
        "baseUrl": "https://token-plan-cn.xiaomimimo.com/v1",
        "apiKeyConfigured": True,
    }
    assert yaml.safe_load(config_path.read_text(encoding="utf-8"))["models"] == [
        {
            "name": "office-default",
            "display_name": "mimo-v2.5",
            "use": "deerflow.models.patched_mimo:PatchedChatMiMo",
            "model": "mimo-v2.5",
            "api_key": "test-secret",
            "base_url": "https://token-plan-cn.xiaomimimo.com/v1",
            "supports_thinking": True,
            "supports_reasoning_effort": False,
            "when_thinking_enabled": {
                "extra_body": {"thinking": {"type": "enabled"}}
            },
        }
    ]
    saved_bootstrap = yaml.safe_load(
        (tmp_path / ".deer-flow" / "users" / "default" / "office-bootstrap.yaml").read_text(
            encoding="utf-8"
        )
    )
    assert saved_bootstrap == {
        "version": 1,
        "company_name": "星河创意工作室",
        "default_model_profile_id": "office-default",
        "thinking_level": "high",
        "model_bindings_version": 2,
    }


def test_bootstrap_restores_saved_default_model_for_employee_creation(tmp_path) -> None:
    from deerflow.office.bootstrap import OfficeBootstrapService

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "sandbox:\n  use: deerflow.sandbox.local:LocalSandboxProvider\n"
        "models:\n"
        "  - name: office-default\n"
        "    use: langchain_openai:ChatOpenAI\n"
        "    model: gpt-test\n"
        "    api_key: test-secret\n",
        encoding="utf-8",
    )
    paths = Paths(tmp_path / ".deer-flow")
    settings_path = paths.user_dir("default") / "office-bootstrap.yaml"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(
        "version: 1\ncompany_name: 星河创意工作室\ndefault_model_profile_id: office-default\n",
        encoding="utf-8",
    )
    employee_service = _EmployeeService()

    service = OfficeBootstrapService(
        paths=paths,
        user_id="default",
        config_path=config_path,
        employee_service=employee_service,
        project_service=_project_service(tmp_path),
    )

    assert service.get_status()["initialized"] is True
    assert employee_service.default_model_profile_ids == ["office-default"]


def test_initialization_requires_persisted_workspace_and_preserves_it_after_model_failure(tmp_path, monkeypatch):
    from deerflow.office.bootstrap import OfficeBootstrapService
    monkeypatch.setattr('app.gateway.routers.office.get_effective_user_id', lambda: 'default')

    config_path = tmp_path / 'config.yaml'
    original = 'sandbox:\n  use: deerflow.sandbox.local:LocalSandboxProvider\nmodels: []\n'
    config_path.write_text(original, encoding='utf-8')
    projects = _project_service(tmp_path)
    employees = _EmployeeService()
    probes = []

    async def test_model(config, model_name):
        probes.append(model_name)
        raise ValueError('测试模型连接失败')

    service = OfficeBootstrapService(paths=projects.paths, user_id='default', config_path=config_path,
                                     employee_service=employees, project_service=projects, test_model=test_model)
    app = FastAPI()
    app.include_router(router)
    app.state.office_bootstrap_service = service
    app.state.office_project_service = projects
    client = TestClient(app)
    payload = {'companyName': '测试办公室', 'modelName': 'mimo-v2.5', 'baseUrl': 'https://example.com/v1', 'apiKey': 'test-key'}
    missing = client.post('/api/office/bootstrap', json=payload)
    assert missing.status_code == 400
    assert missing.json()['detail']['code'] == 'errors.select_and_bind_a_workspace_folder_first'
    assert probes == []
    assert not service._settings_path.exists()
    workspace = tmp_path / '中文 工作区'
    workspace.mkdir()
    bound = client.put('/api/office/project', json={'path': str(workspace)})
    assert bound.status_code == 200
    failed = client.post('/api/office/bootstrap', json=payload)
    assert failed.status_code == 400
    assert probes == ['office-default']
    assert projects.get_binding().path == workspace.resolve()
    assert config_path.read_text(encoding='utf-8') == original
    assert not service._settings_path.exists()
    assert employees.default_model_profile_ids == []

    async def connected(config, model_name):
        pass

    service._test_model = connected
    assert client.post('/api/office/bootstrap', json=payload).json()['initialized'] is True
    assert client.get('/api/office/project').json()['workspace']['projectId'] == bound.json()['workspace']['projectId']
    assert list(workspace.iterdir()) == []


def test_model_settings_map_thinking_level_for_openai_and_mimo(tmp_path) -> None:
    from deerflow.office.bootstrap import OfficeBootstrapService

    config_path = tmp_path / "config.yaml"
    config_path.write_text("models: []\n", encoding="utf-8")
    service = OfficeBootstrapService(
        paths=Paths(tmp_path / ".deer-flow"),
        user_id="default",
        config_path=config_path,
        employee_service=_EmployeeService(),
        project_service=_project_service(tmp_path),
    )

    openai_model = service._model_config_payload({
        "modelName": "gpt-5",
        "thinkingLevel": "medium",
        "baseUrl": "https://api.openai.com/v1",
        "apiKey": "test-secret",
    })
    assert openai_model["use"] == "langchain_openai:ChatOpenAI"
    assert openai_model["supports_reasoning_effort"] is True
    assert openai_model["reasoning_effort"] == "medium"

    mimo_model = service._model_config_payload({
        "modelName": "mimo-v2.5",
        "thinkingLevel": "off",
        "baseUrl": "https://example.com/v1",
        "apiKey": "test-secret",
    })
    assert mimo_model["use"] == "deerflow.models.patched_mimo:PatchedChatMiMo"
    assert mimo_model["supports_thinking"] is False
    assert mimo_model["when_thinking_disabled"] == {
        "extra_body": {"thinking": {"type": "disabled"}}
    }
