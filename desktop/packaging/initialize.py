"""First-install resources only; reuse the existing Office Library persistence API."""
import os
import shutil
import sys
import tempfile
from pathlib import Path


def initialize(resources: Path, data: Path) -> None:
    data.mkdir(parents=True, exist_ok=True)
    for name in ('config.yaml', 'extensions_config.json'):
        target = data / name
        if not target.exists():
            # Publish a complete file atomically, without replacing user settings.
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=data, delete=False) as output, (resources / 'defaults' / name).open('rb') as source:
                    temporary = Path(output.name)
                    shutil.copyfileobj(source, output)
                    output.flush()
                    os.fsync(output.fileno())
                os.link(temporary, target)
            except FileExistsError:
                pass
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)

    print('Office installation configuration is ready.')


if __name__ == '__main__':
    initialize(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve())
