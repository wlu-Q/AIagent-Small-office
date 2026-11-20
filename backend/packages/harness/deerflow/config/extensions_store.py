"""Serialize Gateway mutations against the original, unexpanded extensions file."""
import json
import os
import tempfile
from contextlib import contextmanager
from threading import RLock

from deerflow.config.extensions_config import ExtensionsConfig
from deerflow.config.runtime_paths import project_root

lock = RLock()


def read_raw():
    path = ExtensionsConfig.resolve_config_path()
    return json.loads(path.read_text(encoding="utf-8")) if path else {}


@contextmanager
def edit_extensions():
    with lock:
        path = ExtensionsConfig.resolve_config_path() or project_root() / 'extensions_config.json'
        data = read_raw()
        before = json.dumps(data)
        yield data
        if json.dumps(data) == before:
            return
        ExtensionsConfig.model_validate(data)
        fd, name = tempfile.mkstemp(prefix='.extensions-', dir=path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(data, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, path)
        finally:
            if os.path.exists(name):
                os.unlink(name)
