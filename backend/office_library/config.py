"""Library configuration and local secret protection, independent of chat models."""
from .errors import LibraryInputError
import base64
import ctypes
import hashlib
import os
import sys
from ctypes import wintypes
from urllib.parse import urlsplit

DEFAULTS = {
    'embedding_url': 'https://maas.qianwenaiapi.com/compatible-mode/v1/embeddings',
    'embedding_model': 'qwen3.7-text-embedding-flash',
    'rerank_url': 'https://maas.qianwenaiapi.com/api/v1/services/rerank/text-rerank/text-rerank',
    'rerank_model': 'qwen3.7-text-rerank',
}
MAX_BYTES = 20 * 1024 * 1024
MODEL_KINDS = ('embedding', 'rerank')


def decrypt_config(saved, *, scope=None):
    if 'secret' in saved:
        raise LibraryInputError('请先停服运行 split_library_api_keys.py，迁移资料库共享密钥', code='errors.stop_the_service_and_run_split_library_api_keys_py_to_migrate')
    result = {**DEFAULTS, **saved}
    for kind in MODEL_KINDS:
        secret = result.pop(f'{kind}_secret', '')
        result[f'{kind}_api_key'] = protect(secret, decrypt=True, scope=scope) if secret else os.environ.get(f'OFFICE_LIBRARY_{kind.upper()}_API_KEY', '')
    return result


def validate_config(values):
    result = dict(DEFAULTS)
    for name in DEFAULTS:
        value = values.get(name, result[name])
        if not isinstance(value, str) or not value.strip() or len(value) > 2048:
            raise LibraryInputError('模型配置字段无效', code='errors.invalid_model_configuration_field')
        result[name] = value.strip()
    for name in ('embedding_url', 'rerank_url'):
        url = urlsplit(result[name])
        if url.scheme != 'https' or not url.hostname or url.username or url.password or url.fragment or url.query:
            raise LibraryInputError('模型接口必须是无用户名、密码和查询参数的 HTTPS 地址', code='errors.model_endpoints_must_use_https_without_credentials_or_query_parameters')
    return result


def protect(value: str, *, decrypt=False, scope=None) -> str:
    if sys.platform == 'darwin':
        if not value:
            return ''
        if scope is None:
            raise LibraryInputError('Library Keychain storage requires its data directory')
        from cryptography.fernet import Fernet
        from keyring.backends.macOS import Keyring

        account = hashlib.sha256(str(scope).encode()).hexdigest()
        try:
            keychain = Keyring()
            key = keychain.get_password('small-office.library', account)
            if key is None:
                if decrypt:
                    raise ValueError('Keychain key is missing')
                key = Fernet.generate_key().decode()
                keychain.set_password('small-office.library', account, key)
            cipher = Fernet(key.encode())
            return (cipher.decrypt(value.encode()).decode() if decrypt else
                    cipher.encrypt(value.encode()).decode())
        except Exception as error:
            raise LibraryInputError('无法访问本机保护的资料库密钥', code='errors.unable_to_access_the_locally_protected_library_keys') from error
    if os.name != 'nt':
        if value:
            raise LibraryInputError('此平台请通过 OFFICE_LIBRARY_EMBEDDING_API_KEY 和 OFFICE_LIBRARY_RERANK_API_KEY 环境变量配置密钥', code='errors.use_office_library_embedding_api_key_and_office_library_rerank_api_key')
        return ''

    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]

    raw = base64.b64decode(value) if decrypt else value.encode()
    buffer = ctypes.create_string_buffer(raw)
    source = Blob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    function = ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise LibraryInputError('无法访问本机保护的资料库密钥', code='errors.unable_to_access_the_locally_protected_library_keys')
    try:
        data = ctypes.string_at(target.data, target.size)
        return data.decode() if decrypt else base64.b64encode(data).decode()
    finally:
        ctypes.windll.kernel32.LocalFree(target.data)
