# 本文件属于 Gateway 模型选择模块：只从 DeerFlow AppConfig 派生单次 Run 所需的模型配置。
# 不保存模型密钥、不读取环境变量注册表、不管理 Office 初始化或员工模型分配。
# 关联：office/bootstrap.py 写入 DeerFlow config.yaml，services.py 为 DeerFlow Run 选择当前员工的模型。
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from deerflow.config.app_config import AppConfig

SELECTED_MODEL_NAME = "office-selected-model"


class ModelBindingError(RuntimeError):
    """Raised when a persisted Office Run cannot resolve its configured model."""


def _required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ModelBindingError(f"Model binding {field_name} must be a non-empty string")
    return value.strip()


def resolve_runtime_model_id(app_config: AppConfig, model_name: str) -> str:
    """Return the physical model id behind one logical DeerFlow model name."""

    logical_name = _required_text(model_name, "model name")
    model_config = app_config.get_model_config(logical_name)
    if model_config is None:
        raise ModelBindingError(f"Model binding is unavailable: {logical_name}")
    return _required_text(getattr(model_config, "model", None), "modelId")


def _copy_model(source: Any, *, name: str) -> dict[str, Any]:
    payload = source.model_dump(mode="python", round_trip=True)
    payload["name"] = name
    base_url = payload.get("base_url")
    if isinstance(base_url, str):
        payload["base_url"] = base_url.rstrip("/")
    return payload


def resolve_run_app_config(
    base_config: AppConfig,
    model_profile_id: str,
    *,
    additional_profile_ids: Iterable[str] = (),
) -> AppConfig:
    """Build a run-local DeerFlow config containing only selected employee profiles."""

    profile_id = _required_text(model_profile_id, "profile id")

    def resolve_profile(source_profile_id: str, *, name: str) -> dict[str, Any]:
        source = base_config.get_model_config(source_profile_id)
        if source is None:
            raise ModelBindingError(f"Model profile is unavailable: {source_profile_id}")
        return _copy_model(source, name=name)

    payload = base_config.model_dump(mode="python", round_trip=True)
    models = [resolve_profile(profile_id, name=SELECTED_MODEL_NAME)]
    seen_profiles: set[str] = set()
    for additional_profile_id in additional_profile_ids:
        normalized = _required_text(additional_profile_id, "profile id")
        if normalized in seen_profiles:
            continue
        seen_profiles.add(normalized)
        models.append(resolve_profile(normalized, name=normalized))
    payload["models"] = models
    return AppConfig.model_validate(payload)


def model_profile_exists(base_config: AppConfig, model_profile_id: str) -> bool:
    """Return whether the selected profile is available in DeerFlow configuration."""

    try:
        resolve_run_app_config(base_config, model_profile_id)
    except ModelBindingError:
        return False
    return True
