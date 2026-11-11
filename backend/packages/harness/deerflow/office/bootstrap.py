# 本文件属于 Office 初始化模块：只保存公司名称和默认模型引用，并把模型配置写入 DeerFlow config.yaml。
# 不负责 Agent Loop、Run 调度、消息存储、员工能力路由或前端状态管理。
# 关联：gateway/routers/office.py 暴露初始化 API，employees.py 创建 PM，models/factory.py 负责真实模型调用。
from __future__ import annotations

from deerflow.office.user_errors import OfficeInputError

import asyncio
import inspect
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml
from langchain_core.messages import HumanMessage

from deerflow.config.app_config import AppConfig, reload_app_config
from deerflow.config.paths import Paths
from deerflow.models.factory import create_chat_model
from deerflow.office.projects import OfficeProjectService

OFFICE_DEFAULT_MODEL_PROFILE_ID = "office-default"
_BOOTSTRAP_VERSION = 1
_THINKING_LEVELS = {"off", "low", "medium", "high"}


class OfficeBootstrapService:
    """Own the minimal first-run facts required before an Office can run."""

    def __init__(
        self,
        *,
        paths: Paths,
        user_id: str,
        config_path: Path,
        employee_service: Any,
        project_service: OfficeProjectService,
        test_model: Callable[[AppConfig, str], Awaitable[None]] | None = None,
    ) -> None:
        self.paths = paths
        self.user_id = user_id
        self.config_path = Path(config_path)
        self.employee_service = employee_service
        self.project_service = project_service
        self._test_model = test_model or self._invoke_model
        self._lock = asyncio.Lock()
        self._restore_employee_default_model()
        from deerflow.office.model_management import OfficeModelService
        self.models = OfficeModelService(self)
        self.employee_service.model_profile_selectable = lambda profile_id: any(
            model['id'] == profile_id for model in self.models.list()['models']
        )

    @property
    def _settings_path(self) -> Path:
        return self.paths.user_dir(self.user_id) / "office-bootstrap.yaml"

    @staticmethod
    def _required_text(payload: dict[str, Any], key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{key} must be a non-empty string")
        return value.strip()

    @classmethod
    def _base_url(cls, payload: dict[str, Any]) -> str:
        value = cls._required_text(payload, "baseUrl").rstrip("/")
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("baseUrl must be an HTTP(S) URL")
        return value

    @classmethod
    def _thinking_level(cls, payload: dict[str, Any]) -> str:
        value = str(payload.get("thinkingLevel") or "high").strip().lower()
        if value not in _THINKING_LEVELS:
            raise ValueError("thinkingLevel must be one of: off, low, medium, high")
        return value

    @staticmethod
    def _apply_thinking_settings(model_payload: dict[str, Any], *, model_name: str, level: str) -> None:
        """Map the Office setting onto provider fields already supported by DeerFlow."""
        model_payload.pop("reasoning_effort", None)
        model_payload.pop("when_thinking_enabled", None)
        model_payload.pop("when_thinking_disabled", None)

        normalized_name = model_name.lower()
        if normalized_name.startswith("mimo-"):
            model_payload["supports_thinking"] = level != "off"
            model_payload["supports_reasoning_effort"] = False
            if level == "off":
                model_payload["when_thinking_disabled"] = {
                    "extra_body": {"thinking": {"type": "disabled"}}
                }
            else:
                model_payload["when_thinking_enabled"] = {
                    "extra_body": {"thinking": {"type": "enabled"}}
                }
            return

        model_payload["supports_thinking"] = False
        model_payload["supports_reasoning_effort"] = normalized_name.startswith(("gpt-5", "o1", "o3", "o4"))
        if model_payload["supports_reasoning_effort"] and level != "off":
            model_payload["reasoning_effort"] = level

    @staticmethod
    def _model_provider(model_name: str) -> str:
        if model_name.lower().startswith("mimo-"):
            return "deerflow.models.patched_mimo:PatchedChatMiMo"
        return "langchain_openai:ChatOpenAI"

    def _read_config_payload(self) -> dict[str, Any]:
        if not self.config_path.is_file():
            raise FileNotFoundError(f"DeerFlow config does not exist: {self.config_path}")
        payload = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}
        if not isinstance(payload, dict):
            raise ValueError("DeerFlow config must contain a mapping")
        return payload

    def _model_config_payload(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        model_name = self._required_text(payload, "modelName")
        thinking_level = self._thinking_level(payload)
        model_payload = {}
        api_key = str(payload.get("apiKey") or "").strip()
        if not api_key:
            raise ValueError("apiKey must be a non-empty string")
        model_payload.update({
            "name": OFFICE_DEFAULT_MODEL_PROFILE_ID,
            "display_name": model_name,
            "use": self._model_provider(model_name),
            "model": model_name,
            "api_key": api_key,
            "base_url": self._base_url(payload),
        })
        self._apply_thinking_settings(model_payload, model_name=model_name, level=thinking_level)
        return model_payload

    def _config_with_default_model(
        self,
        source: dict[str, Any],
        model_config: dict[str, Any],
    ) -> tuple[dict[str, Any], AppConfig]:
        models = source.get("models")
        if models is None:
            models = []
        if not isinstance(models, list):
            raise ValueError("DeerFlow config models must be a list")
        updated = dict(source)
        updated["models"] = [
            model
            for model in models
            if not isinstance(model, dict)
            or str(model.get("name") or "").strip() != OFFICE_DEFAULT_MODEL_PROFILE_ID
        ] + [model_config]
        return updated, AppConfig.model_validate(updated)

    @staticmethod
    def _write_yaml_atomically(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary_path.write_text(
                yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
            temporary_path.replace(path)
        finally:
            temporary_path.unlink(missing_ok=True)

    @staticmethod
    def _public_model(config: AppConfig, profile_id: str) -> dict[str, str] | None:
        model = config.get_model_config(profile_id)
        if model is None:
            return None
        return {
            "name": model.name,
            "model": model.model,
            "displayName": model.display_name or model.model,
        }

    def _read_settings(self) -> dict[str, Any] | None:
        if not self._settings_path.is_file():
            return None
        payload = yaml.safe_load(self._settings_path.read_text(encoding="utf-8")) or {}
        return payload if isinstance(payload, dict) else None

    def _restore_employee_default_model(self) -> None:
        settings = self._read_settings()
        profile_id = str((settings or {}).get("default_model_profile_id") or "").strip()
        if not profile_id:
            return
        config = AppConfig.from_file(str(self.config_path))
        if self._public_model(config, profile_id) is not None:
            self.employee_service.set_default_model_profile_id(profile_id)
            self._migrate_model_bindings(settings, profile_id)

    def _migrate_model_bindings(self, settings: dict, profile_id: str) -> None:
        """One-time conversion of the old shared binding, preserving concrete Run IDs."""
        source = self._read_config_payload()
        records = source.setdefault('office_model_records', {}).setdefault(self.user_id, {})
        if profile_id not in records:
            records[profile_id] = {'created_at': None}
            self._write_yaml_atomically(self.config_path, source)
        if settings.get('model_bindings_version') == 2:
            return
        migrate = getattr(self.employee_service, 'migrate_global_model_bindings', None)
        if migrate:
            migrate(profile_id)
        self._write_yaml_atomically(self._settings_path, {**settings, 'model_bindings_version': 2})

    def get_status(self) -> dict[str, Any]:
        settings = self._read_settings()
        if settings is None:
            return {
                "initialized": False,
                "companyName": None,
                "defaultModel": None,
                "thinkingLevel": "high",
                "baseUrl": None,
                "apiKeyConfigured": False,
            }
        company_name = str(settings.get("company_name") or "").strip()
        profile_id = str(settings.get("default_model_profile_id") or "").strip()
        if not company_name or not profile_id:
            return {
                "initialized": False,
                "companyName": None,
                "defaultModel": None,
                "thinkingLevel": "high",
                "baseUrl": None,
                "apiKeyConfigured": False,
            }
        config = AppConfig.from_file(str(self.config_path))
        model = self._public_model(config, profile_id)
        if model is None:
            return {
                "initialized": False,
                "companyName": None,
                "defaultModel": None,
                "thinkingLevel": "high",
                "baseUrl": None,
                "apiKeyConfigured": False,
            }
        model_config = config.get_model_config(profile_id)
        return {
            "initialized": True,
            "companyName": company_name,
            "terminalAccessMode": str(settings.get('terminal_access_mode') or 'approval'),
            "defaultModel": model,
            "thinkingLevel": str(settings.get("thinking_level") or "high"),
            "baseUrl": getattr(model_config, "base_url", None),
            "apiKeyConfigured": bool(getattr(model_config, "api_key", None)),
        }

    async def test_connection(self, payload: dict[str, Any]) -> dict[str, str]:
        source = self._read_config_payload()
        model_payload = self._model_config_payload(payload)
        _, config = self._config_with_default_model(source, model_payload)
        await self._test_model(config, OFFICE_DEFAULT_MODEL_PROFILE_ID)
        model = self._public_model(config, OFFICE_DEFAULT_MODEL_PROFILE_ID)
        if model is None:
            raise RuntimeError("Configured default model is unavailable")
        return model

    async def initialize(self, payload: dict[str, Any]) -> dict[str, Any]:
        company_name = self._required_text(payload, "companyName")
        async with self._lock:
            if self.project_service.get_binding() is None:
                raise OfficeInputError('请先选择并绑定工作区目录', code='errors.select_and_bind_a_workspace_folder_first')
            source = self._read_config_payload()
            model_payload = self._model_config_payload(payload)
            updated, config = self._config_with_default_model(source, model_payload)
            await self._test_model(config, OFFICE_DEFAULT_MODEL_PROFILE_ID)
            self._write_yaml_atomically(self.config_path, updated)
            try:
                reloaded = reload_app_config(str(self.config_path))
                self.employee_service.set_default_model_profile_id(
                    OFFICE_DEFAULT_MODEL_PROFILE_ID,
                )
                await self.employee_service.ensure_builtin_pm(
                    default_model_profile_id=OFFICE_DEFAULT_MODEL_PROFILE_ID,
                )
            except Exception:
                self._write_yaml_atomically(self.config_path, source)
                reload_app_config(str(self.config_path))
                raise
            self._write_yaml_atomically(
                self._settings_path,
                {
                    "version": _BOOTSTRAP_VERSION,
                    "company_name": company_name,
                    "default_model_profile_id": OFFICE_DEFAULT_MODEL_PROFILE_ID,
                    "thinking_level": self._thinking_level(payload),
                },
            )
            self._migrate_model_bindings(self._read_settings(), OFFICE_DEFAULT_MODEL_PROFILE_ID)
            model = self._public_model(reloaded, OFFICE_DEFAULT_MODEL_PROFILE_ID)
            if model is None:
                raise RuntimeError("Configured default model is unavailable")
            return {
                "initialized": True,
                "companyName": company_name,
                "defaultModel": model,
                "thinkingLevel": self._thinking_level(payload),
                "baseUrl": self._base_url(payload),
                "apiKeyConfigured": True,
            }

    async def update_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Company settings never mutate or retest a saved model."""
        if not {'companyName'} <= set(payload) <= {'companyName', 'terminalAccessMode', 'confirmFullAccess'}:
            raise OfficeInputError('只接受办公室名称和终端权限设置', code='errors.only_office_name_and_terminal_permission_settings_are_accepted')
        mode = payload.get('terminalAccessMode')
        if mode is not None and mode not in {'approval', 'full_access'}:
            raise ValueError('Invalid terminal access mode')
        company_name = self._required_text(payload, 'companyName')
        async with self._lock:
            settings = self._read_settings()
            if not settings:
                raise OfficeInputError('请先完成办公室初始化', code='errors.complete_office_setup_first')
            if mode == 'full_access' and settings.get('terminal_access_mode', 'approval') != mode and payload.get('confirmFullAccess') is not True:
                raise OfficeInputError('请先确认完全访问会使用本机权限，可能影响项目外文件或系统环境', code='errors.confirm_that_full_access_uses_local_permissions_and_may_affect_files_or')
            self._write_yaml_atomically(self._settings_path, {**settings, 'company_name': company_name,
                'terminal_access_mode': mode or settings.get('terminal_access_mode', 'approval')})
            return self.get_status()

    @staticmethod
    async def _invoke_model(config: AppConfig, model_name: str) -> None:
        model = create_chat_model(
            name=model_name,
            thinking_enabled=None,
            app_config=config,
        )
        await model.ainvoke([HumanMessage(content="Reply with OK.")])
