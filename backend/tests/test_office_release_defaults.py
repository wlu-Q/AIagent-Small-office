"""Exercise release preparation in a temporary repository, without touching installers."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'desktop/packaging' / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_prepare_uses_only_public_inputs(tmp_path, monkeypatch):
    prepare = load_script('prepare')
    # A complete minimal source tree, deliberately without private config or DB.
    for name in ('main.cjs', 'preload.cjs', 'gateway-env.cjs', 'process-lifecycle.cjs', 'dev-startup.cjs', 'packaged-entry.cjs', 'localization.cjs'):
        path = tmp_path / 'desktop' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('// fixture', encoding='utf-8')
    for name in ('backend/app/test.py', 'backend/office_library/test.py',
                 'backend/packages/harness/deerflow/test.py', 'client/src/test.ts', 'client/dist/index.html',
                 'contracts/test.json', 'skills/public/example/SKILL.md', 'LICENSE', 'backend/DEERFLOW_LICENSE',
                 'node_modules/i18next/dist/umd/i18next.js', 'node_modules/i18next/LICENSE'):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('fixture', encoding='utf-8')
    (tmp_path / 'package.json').write_text(json.dumps({'name': 'office', 'version': '1', 'description': 'test'}))
    for name in ('node_modules/i18next/package.json', 'node_modules/i18next/dist/umd/i18next.js',
                 'desktop/localization.cjs', 'contracts/locales/en.json', 'contracts/locales/zh-CN.json'):
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / name).read_bytes())
    defaults = tmp_path / 'desktop/packaging/defaults'
    defaults.mkdir(parents=True)
    for name in ('config.yaml', 'extensions_config.json'):
        (defaults / name).write_bytes((ROOT / 'desktop/packaging/defaults' / name).read_bytes())
    (tmp_path / 'desktop/packaging/initialize.py').write_bytes((ROOT / 'desktop/packaging/initialize.py').read_bytes())
    resources = tmp_path / 'output/packaging/resources'
    for name in ('python/python.exe', 'node/node.exe', 'git/cmd/git.exe', 'powershell/pwsh.exe'):
        path = resources / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'fixture')
    monkeypatch.setattr(prepare, 'ROOT', tmp_path)
    monkeypatch.setattr(prepare, 'BUILD', tmp_path / 'output/packaging')
    monkeypatch.setattr(prepare, 'RESOURCES', resources)
    prepare.prepare()
    assert (resources / 'defaults/config.yaml').read_bytes() == (defaults / 'config.yaml').read_bytes()
    assert (resources / 'LICENSE').read_bytes() == (tmp_path / 'LICENSE').read_bytes()
    assert (resources / 'DEERFLOW_LICENSE').read_bytes() == (tmp_path / 'backend/DEERFLOW_LICENSE').read_bytes()
    assert not (resources / 'defaults/library.json').exists()
    manifest = json.loads((resources / 'release-manifest.json').read_text())
    assert not manifest['search_shared_key_included'] and not manifest['rag_shared_key_included']
    # Even an unreadable private config must have no effect on preparation.
    (tmp_path / 'config.yaml').write_text('malformed: [do not read developer configuration')
    prepare.prepare()
    assert (resources / 'defaults/config.yaml').read_bytes() == (defaults / 'config.yaml').read_bytes()
    search = next(tool for tool in yaml.safe_load((resources / 'defaults/config.yaml').read_text())['tools'] if tool['name'] == 'web_search')
    assert 'api_key' not in search and search['fallback'] == 'ddgs'
    script = "const locale=require(process.argv[1]).createLocalization(process.argv[2], 'en'); process.stdout.write(locale.t('ui.save'))"
    result = subprocess.run(['node', '-e', script, str(tmp_path / 'output/packaging/app/desktop/localization.cjs'), str(tmp_path / 'profile')],
                            cwd=tmp_path, check=True, capture_output=True, encoding='utf-8')
    assert result.stdout == 'Save'


def test_first_install_without_keys_and_reinitialization_preserve_user_settings(tmp_path, monkeypatch):
    from office_library.service import LibraryService
    for kind in ('EMBEDDING', 'RERANK'):
        monkeypatch.delenv(f'OFFICE_LIBRARY_{kind}_API_KEY', raising=False)
    resources = ROOT / 'desktop/packaging'
    data = tmp_path / 'profile'
    # The initializer must run standalone without importing backend modules.
    subprocess.run([sys.executable, str(resources / 'initialize.py'), str(resources), str(data)],
                   cwd=tmp_path, check=True, capture_output=True)
    assert not (data / 'state').exists()
    library = LibraryService(data / 'state/users/default/office-library')
    assert not library.settings()['embeddingApiKeyConfigured']
    assert not library.settings()['rerankApiKeyConfigured']
    from office_library.providers import ProviderError
    import pytest
    with pytest.raises(ProviderError, match='请先配置向量模型'):
        library.test_connection()
    library.save_settings({'embedding_api_key': 'test-user-embedding', 'rerank_api_key': 'test-user-rerank'})
    (data / 'config.yaml').write_text('models: []\n# user owned\n', encoding='utf-8')
    load_script('initialize').initialize(resources, data)
    assert '# user owned' in (data / 'config.yaml').read_text(encoding='utf-8')
    reopened = LibraryService(library.root)
    assert reopened._config()['embedding_api_key'] == 'test-user-embedding'
    assert reopened._config()['rerank_api_key'] == 'test-user-rerank'
