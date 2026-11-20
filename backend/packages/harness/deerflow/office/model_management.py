"""Office model catalogue and validation; runtime models remain in DeerFlow config."""
from __future__ import annotations

from deerflow.office.user_errors import OfficeBusyError, OfficeInputError, OfficeNotFoundError

import asyncio
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from langchain_core.messages import HumanMessage, ToolMessage

from deerflow.config.app_config import AppConfig, reload_app_config
from deerflow.models.factory import create_chat_model


# Official OpenAI-compatible endpoints. Addresses remain editable for private gateways.
PROVIDERS = (
    ('openai', 'OpenAI', 'https://api.openai.com/v1', 'langchain_openai:ChatOpenAI'),
    ('deepseek', 'DeepSeek', 'https://api.deepseek.com', 'deerflow.models.patched_deepseek:PatchedChatDeepSeek'),
    ('mimo', 'MiMo', 'https://api.xiaomimimo.com/v1', 'deerflow.models.patched_mimo:PatchedChatMiMo'),
    ('kimi', 'Kimi', 'https://api.moonshot.cn/v1', 'deerflow.models.patched_deepseek:PatchedChatDeepSeek'),
    ('glm', 'GLM', 'https://open.bigmodel.cn/api/paas/v4', 'deerflow.models.patched_deepseek:PatchedChatDeepSeek'),
    ('doubao', '豆包', 'https://ark.cn-beijing.volces.com/api/v3', 'deerflow.models.patched_deepseek:PatchedChatDeepSeek'),
    ('minimax', 'MiniMax', 'https://api.minimax.cn/v1', 'deerflow.models.patched_minimax:PatchedChatMiniMax'),
    ('qwen', 'Qwen', 'https://dashscope.aliyuncs.com/compatible-mode/v1', 'deerflow.models.reasoning_chat_openai:ChatQwen'),
)
PROVIDER_MAP = {item[0]: item for item in PROVIDERS}

# Vendor-wide UI policy. Values are passed through, never translated between levels.
THINKING_LEVELS = {
    'openai': ('none', 'low', 'medium', 'high', 'xhigh', 'max'),
    'deepseek': ('low', 'high', 'max'),
    'mimo': ('enabled', 'disabled'),
    'qwen': ('default', 'enabled', 'disabled'),
    'kimi': ('low', 'high', 'max'),
    'glm': ('low', 'high', 'max'),
    'doubao': ('minimal', 'low', 'medium', 'high'),
    'minimax': ('low', 'medium', 'high', 'xhigh', 'max'),
}
DEFAULT_THINKING_LEVELS = {'mimo': 'enabled', 'qwen': 'default'}
THINKING_LABELS = {'default': '模型默认', 'none': '关闭思考', 'minimal': '最低', 'low': '低', 'medium': '中',
                   'high': '高', 'xhigh': '极高', 'max': '最高', 'enabled': '开启思考', 'disabled': '关闭思考'}


def apply_thinking_settings(model: dict, provider: str, level: str) -> None:
    if not isinstance(level, str) or level not in THINKING_LEVELS[provider]:
        raise OfficeInputError('请选择此模型商支持的思考配置', code='errors.select_a_reasoning_setting_supported_by_this_provider')
    model['supports_thinking'] = True
    model['supports_reasoning_effort'] = provider not in {'mimo', 'qwen'}
    model['default_thinking_enabled'] = level not in {'none', 'disabled'}
    model.pop('thinking', None)
    # Leave unrelated provider options intact when switching modes.
    controls = {'thinking', 'enable_thinking'} if provider == 'qwen' else {'thinking'}
    for key in ('extra_body', 'when_thinking_enabled', 'when_thinking_disabled'):
        settings = dict(model.get(key) or {})
        settings.pop('reasoning_effort', None)
        for field in controls:
            settings.pop(field, None)
        if isinstance(settings.get('extra_body'), dict):
            body = {k: v for k, v in settings.pop('extra_body').items() if k not in controls}
            if body:
                settings['extra_body'] = body
        if settings:
            model[key] = settings
        else:
            model.pop(key, None)
    if provider in {'mimo', 'qwen'}:
        model.pop('reasoning_effort', None)
        if level == 'default':
            model.pop('default_thinking_enabled', None)
            return
        for key, mode in [('when_thinking_enabled', 'enabled'), ('when_thinking_disabled', 'disabled')]:
            settings = model.setdefault(key, {})
            control = {'enable_thinking': mode == 'enabled'} if provider == 'qwen' else {'thinking': {'type': mode}}
            settings['extra_body'] = {**settings.get('extra_body', {}), **control}
    else:
        model['reasoning_effort'] = level
        if provider in {'deepseek', 'doubao'}:
            settings = model.setdefault('when_thinking_enabled', {})
            settings['extra_body'] = {**settings.get('extra_body', {}), 'thinking': {'type': 'enabled'}}
        if provider == 'openai':
            model.setdefault('when_thinking_disabled', {})['reasoning_effort'] = 'none'


def thinking_summary(model: dict, provider: str | None) -> dict:
    level = model.get('reasoning_effort')
    if not level:
        enabled = model.get('default_thinking_enabled')
        if isinstance(enabled, bool):
            level = 'enabled' if enabled else 'disabled'
        elif (model.get('when_thinking_enabled') or {}).get('extra_body', {}).get('thinking', {}).get('type') == 'enabled':
            level = 'enabled'
        elif (model.get('when_thinking_disabled') or {}).get('extra_body', {}).get('thinking', {}).get('type') == 'disabled':
            level = 'disabled'
    if level is None and provider == 'qwen':
        level = 'default'
    return {'thinkingLevel': level, 'thinkingLabel': THINKING_LABELS.get(level, '模型默认')}


class ModelProbeError(OfficeInputError):
    """Safe local validation text, distinct from provider exceptions."""


def public_providers() -> list[dict]:
    return [{'id': key, 'name': label, 'baseUrl': url,
             'thinkingOptions': [{'value': value, 'label': THINKING_LABELS[value]} for value in THINKING_LEVELS[key]],
             'defaultThinkingLevel': DEFAULT_THINKING_LEVELS.get(key, 'high')} for key, label, url, _ in PROVIDERS]


def model_payload(payload: dict, profile_id: str) -> dict:
    allowed = {'requestId', 'provider', 'modelName', 'baseUrl', 'apiKey', 'supportsVision', 'thinkingLevel'}
    if set(payload) - allowed:
        raise OfficeInputError('包含不支持的模型配置字段', code='errors.unsupported_model_configuration_fields')
    provider_id = payload.get('provider')
    provider = PROVIDER_MAP.get(provider_id) if isinstance(provider_id, str) else None
    if provider is None:
        raise OfficeInputError('请选择支持的模型商', code='errors.select_a_supported_model_provider')
    result = {'name': profile_id, 'use': provider[3], **_connection_fields(payload)}
    if provider[0] == 'minimax':
        result['extra_body'] = {'reasoning_split': True}
    apply_thinking_settings(result, provider[0], payload.get('thinkingLevel', DEFAULT_THINKING_LEVELS.get(provider_id, 'high')))
    return result


def _connection_fields(payload: dict) -> dict:
    def required(key, label, code):
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            raise OfficeInputError(f'请填写{label}', code=code)
        return value.strip()
    name = required('modelName', '模型名称', 'errors.model_name_required')
    key = required('apiKey', 'API Key', 'errors.api_key_required')
    url = required('baseUrl', 'API 地址', 'errors.api_url_required').rstrip('/')
    parsed = urlsplit(url)
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise OfficeInputError('API 地址必须是有效的 HTTP(S) 基础地址，不能包含密钥、查询参数或片段', code='errors.use_an_http_s_base_url_without_credentials_query_parameters_or_fragments')
    vision = payload.get('supportsVision')
    if not isinstance(vision, bool):
        raise OfficeInputError('请选择是否支持图片输入', code='errors.choose_whether_the_model_supports_image_input')
    return {'display_name': name, 'model': name, 'api_key': key, 'base_url': url, 'supports_vision': vision}


async def verify_tool_roundtrip(config: AppConfig, profile_id: str) -> None:
    """Two model calls, one harmless local tool. Never touch a user's project."""
    model = create_chat_model(name=profile_id, thinking_enabled=None, app_config=config, attach_tracing=False, timeout=45, max_retries=0)
    schema = {'type': 'function', 'function': {'name': 'office_connection_probe',
        'description': 'Return a connection verification code. Call this tool to obtain the code.',
        'parameters': {'type': 'object', 'properties': {}, 'additionalProperties': False}}}
    messages = [HumanMessage(content='Call office_connection_probe once, then reply with exactly its verification code. Do not guess the code.')]
    reply = await model.bind_tools([schema]).ainvoke(messages)
    calls = getattr(reply, 'tool_calls', [])
    if len(calls) != 1 or calls[0].get('name') != 'office_connection_probe' or not calls[0].get('id') or calls[0].get('args') != {}:
        raise ModelProbeError('模型未正确调用测试工具，请检查模型是否支持工具调用', code='errors.the_model_did_not_call_the_test_tool_correctly_check_tool_calling')
    code = secrets.token_hex(8)
    result = await model.bind_tools([schema]).ainvoke([*messages, reply,
        ToolMessage(content=code, tool_call_id=calls[0]['id'], name='office_connection_probe')])
    content = result.content
    text = content if isinstance(content, str) else ''.join(block.get('text', '') for block in content if isinstance(block, dict))
    if getattr(result, 'tool_calls', []) or code not in text:
        raise ModelProbeError('模型未正确接收工具结果，请检查接口的多轮工具调用兼容性', code='errors.the_model_did_not_receive_the_tool_result_correctly_check_multi_turn')


def connection_error_detail(error: Exception) -> tuple[str, str]:
    # Provider exception bodies can echo credentials or URLs; never expose them.
    status = getattr(error, 'status_code', None)
    if status in (401, 403):
        return ('errors.provider_auth', '认证或权限不足，请检查 API Key 与模型访问权限')
    if status == 404:
        return ('errors.provider_not_found', '模型或接口不存在，请检查模型名称和 API 地址')
    if status == 429:
        return ('errors.provider_rate_limit', '请求受限或余额不足，请检查额度后重试')
    if isinstance(error, TimeoutError) or 'timeout' in type(error).__name__.lower():
        return ('errors.provider_timeout', '连接超时，请检查网络和 API 地址后重试')
    if status == 400:
        return ('errors.provider_bad_request', '接口拒绝请求，请检查模型名称、所选思考配置及工具调用支持')
    return ('errors.provider_verification', '无法完成模型验证，请检查地址、网络及接口兼容性')


class OfficeModelService:
    def __init__(self, bootstrap, verify=None):
        self.bootstrap = bootstrap
        self.verify = verify or verify_tool_roundtrip

    def list(self) -> dict[str, Any]:
        source = self.bootstrap._read_config_payload()
        settings = self.bootstrap._read_settings() or {}
        default_id = settings.get('default_model_profile_id')
        records = (source.get('office_model_records') or {}).get(self.bootstrap.user_id, {})
        result = []
        # Only Office-owned registrations plus this user's existing default.
        for item in source.get('models', []):
            profile_id = item.get('name')
            if profile_id not in records and profile_id != default_id:
                continue
            meta = records.get(profile_id, {})
            provider = meta.get('provider')
            if not provider:
                name = str(item.get('model', '')).lower()
                provider = next((p for prefix, p in [('gpt', 'openai'), ('o1', 'openai'), ('o3', 'openai'),
                    ('o4', 'openai'), ('deepseek', 'deepseek'), ('mimo', 'mimo'), ('kimi', 'kimi'),
                    ('moonshot', 'kimi'), ('glm', 'glm'), ('doubao', 'doubao'), ('minimax', 'minimax')]
                    if name.startswith(prefix)), None)
            result.append({'id': profile_id, 'provider': provider,
                'providerName': PROVIDER_MAP[provider][1] if provider in PROVIDER_MAP else '历史配置',
                'modelName': item.get('model'), 'createdAt': meta.get('created_at'),
                'supportsVision': bool(item.get('supports_vision')), 'isDefault': profile_id == default_id,
                **thinking_summary(item, provider)})
        return {'models': result, 'providers': public_providers()}

    def _owned_model(self, profile_id: str) -> tuple[dict, dict]:
        source = self.bootstrap._read_config_payload()
        records = (source.get('office_model_records') or {}).get(self.bootstrap.user_id, {})
        default_id = (self.bootstrap._read_settings() or {}).get('default_model_profile_id')
        if profile_id not in records and profile_id != default_id:
            raise OfficeNotFoundError('模型不存在或不属于当前办公室', code='errors.model_not_found_in_this_office')
        model = next((item for item in source.get('models', []) if item.get('name') == profile_id), None)
        if model is None:
            raise OfficeNotFoundError('模型不存在或不属于当前办公室', code='errors.model_not_found_in_this_office')
        return source, model

    async def _require_idle(self, profile_id: str) -> None:
        employees = self.bootstrap.employee_service
        for employee in await employees.list_employees():
            if employee.get('modelProfileId') == profile_id and await employees.is_employee_working(employee['id']):
                raise OfficeBusyError('有员工正在使用此模型工作或等待回复，请结束任务后再修改', code='errors.an_employee_is_using_this_model_or_waiting_for_a_reply_finish')

    async def detail(self, profile_id: str) -> dict:
        _, model = self._owned_model(profile_id)
        summary = next(item for item in self.list()['models'] if item['id'] == profile_id)
        reason = ''
        try:
            await self._require_idle(profile_id)
        except OfficeBusyError as error:
            reason = error.public_code
        return {**summary, 'baseUrl': model.get('base_url') or model.get('api_base') or model.get('openai_api_base') or '',
                'apiKeyConfigured': bool(model.get('api_key')), 'editable': not reason, 'editReasonCode': reason}

    async def update(self, profile_id: str, payload: dict) -> dict:
        if set(payload) - {'modelName', 'baseUrl', 'apiKey', 'supportsVision', 'thinkingLevel'}:
            raise OfficeInputError('包含不支持的模型配置字段', code='errors.unsupported_model_configuration_fields')
        key = payload.get('apiKey', '')
        if not isinstance(key, str):
            raise OfficeInputError('API Key 必须是文本，留空表示不修改', code='errors.api_key_must_be_text_leave_blank_to_keep_the_existing_key')
        async with self.bootstrap._lock:
            source, existing = self._owned_model(profile_id)
            await self._require_idle(profile_id)
            fields = _connection_fields({**payload, 'apiKey': key.strip() or existing.get('api_key')})
            candidate = {**existing, **fields}
            if 'thinkingLevel' in payload:
                provider = next(item['provider'] for item in self.list()['models'] if item['id'] == profile_id)
                if provider not in PROVIDER_MAP:
                    raise OfficeInputError('无法识别此历史配置的模型商，不能修改思考配置', code='errors.cannot_edit_reasoning_settings_for_an_unrecognized_model_provider')
                apply_thinking_settings(candidate, provider, payload['thinkingLevel'])
            # Keep provider-specific options; normalize only the editable endpoint.
            candidate.pop('api_base', None)
            candidate.pop('openai_api_base', None)
            updated = {**source, 'models': [candidate if item.get('name') == profile_id else item for item in source['models']]}
            connection_changed = any(candidate.get(field) != existing.get(field) for field in (
                'model', 'api_key', 'reasoning_effort', 'default_thinking_enabled', 'supports_thinking',
                'supports_reasoning_effort', 'when_thinking_enabled', 'when_thinking_disabled', 'thinking', 'extra_body')) or fields['base_url'] != str(
                existing.get('base_url') or existing.get('api_base') or existing.get('openai_api_base') or '').rstrip('/')
            try:
                config = AppConfig.model_validate(updated)
                if connection_changed:
                    async with asyncio.timeout(90):
                        await self.verify(config, profile_id)
            except ModelProbeError:
                raise
            except Exception as error:
                code, message = connection_error_detail(error)
                raise OfficeInputError(message, code=code) from None
            # Verification yields to active runs. Recheck before replacing their profile.
            await self._require_idle(profile_id)
            try:
                self.bootstrap._write_yaml_atomically(self.bootstrap.config_path, updated)
                reload_app_config(str(self.bootstrap.config_path))
            except Exception:
                try:
                    self.bootstrap._write_yaml_atomically(self.bootstrap.config_path, source)
                    reload_app_config(str(self.bootstrap.config_path))
                except Exception:
                    raise OfficeBusyError('模型保存失败且恢复未完成，请检查配置文件与磁盘状态', code='errors.model_save_and_recovery_failed_check_the_configuration_file_and_disk') from None
                raise OfficeInputError('模型配置保存失败，已恢复原配置', code='errors.model_settings_failed_to_save_original_settings_restored') from None
            return self.list()

    async def add(self, payload: dict) -> dict:
        try:
            request_id = str(uuid.UUID(str(payload.get('requestId', ''))))
        except ValueError:
            raise OfficeInputError('添加请求标识无效，请重新打开添加弹窗', code='errors.invalid_add_request_id_reopen_the_add_model_dialog') from None
        profile_id = 'office-model-' + request_id
        model = model_payload(payload, profile_id)
        async with self.bootstrap._lock:
            source = self.bootstrap._read_config_payload()
            records = source.setdefault('office_model_records', {}).setdefault(self.bootstrap.user_id, {})
            existing = next((m for m in source.get('models', []) if m.get('name') == profile_id), None)
            if existing is not None:
                if profile_id in records and existing == model and records[profile_id]['provider'] == payload['provider']:
                    return self.list()
                raise OfficeInputError('请求标识已使用，请重新打开添加弹窗', code='errors.request_id_already_used_reopen_the_add_model_dialog')
            updated = {**source, 'models': [*source.get('models', []), model]}
            try:
                config = AppConfig.model_validate(updated)
                async with asyncio.timeout(90):
                    await self.verify(config, profile_id)
            except ModelProbeError:
                raise
            except Exception as error:
                code, message = connection_error_detail(error)
                raise OfficeInputError(message, code=code) from None
            records[profile_id] = {'provider': payload['provider'], 'created_at': datetime.now(timezone.utc).isoformat()}
            self.bootstrap._write_yaml_atomically(self.bootstrap.config_path, updated)
            try:
                reload_app_config(str(self.bootstrap.config_path))
            except Exception:
                # Restore the original file, including metadata, on reload failure.
                records.pop(profile_id, None)
                self.bootstrap._write_yaml_atomically(self.bootstrap.config_path, source)
                reload_app_config(str(self.bootstrap.config_path))
                raise OfficeInputError('模型配置加载失败，已恢复原配置', code='errors.model_settings_failed_to_load_original_settings_restored') from None
            return self.list()

    async def set_default(self, profile_id: str) -> dict:
        async with self.bootstrap._lock:
            if not any(m['id'] == profile_id for m in self.list()['models']):
                raise OfficeInputError('模型不存在或不属于当前办公室', code='errors.model_not_found_in_this_office')
            settings = self.bootstrap._read_settings()
            if not settings:
                raise OfficeInputError('请先完成办公室初始化', code='errors.complete_office_setup_first')
            self.bootstrap._write_yaml_atomically(self.bootstrap._settings_path,
                {**settings, 'default_model_profile_id': profile_id})
            try:
                self.bootstrap.employee_service.set_default_model_profile_id(profile_id)
            except Exception:
                self.bootstrap._write_yaml_atomically(self.bootstrap._settings_path, settings)
                raise OfficeInputError('默认模型切换失败，已保留原设置', code='errors.default_model_change_failed_original_settings_kept') from None
            return self.list()
