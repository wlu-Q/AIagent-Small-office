"""Build runtime frame coordinates without redrawing/resizing approved artwork.

Run after prepare-employee-reactions.py has prepared Ethan. The other eight
sheets are copied byte-for-byte from the approved preview.
"""
import hashlib
import json
import shutil
from pathlib import Path

from PIL import Image

CLIENT = Path(__file__).resolve().parents[1]
ASSETS = CLIENT / 'public/assets/character/reactions'
PREVIEW = CLIENT.parent / 'output/imagegen/employee-actions-20261106-preview'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_catalog():
    catalog = {'ethan': {}}
    ethan = json.loads((ASSETS / 'ethan/frames.json').read_text(encoding='utf-8'))
    for action, asset in ethan.items():
        width, height = asset['frameWidth'], asset['frameHeight']
        frames = [{
            'source': [i % asset['columns'] * width, i // asset['columns'] * height, width, height],
            'anchorX': asset['anchorX'], 'anchorY': asset['anchorY'],
            'bodyBounds': asset['bodyBounds'][i], 'contentTop': asset['contentTop'][i],
            # Only the old Ethan jump includes a baked shadow below the feet.
            'visibleHeight': asset['anchorY'] if action == 'jump' else height,
        } for i in range(asset['frameCount'])]
        catalog['ethan'][action] = {
            'image': action, 'imageSha256': digest(ASSETS / f'ethan/{action}.png'),
            'scale': asset['scale'], 'frames': frames, 'steps': asset['steps'],
            'sourceFps': asset['sourceFps'], 'playbackFps': asset['playbackFps'],
        }

    preview = json.loads((PREVIEW / 'metadata.json').read_text(encoding='utf-8'))
    for character in preview['names']:
        source = PREVIEW / preview['characters'][character]['sheet']
        target = ASSETS / character / 'sheet.png'
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        image = Image.open(source).convert('RGBA')
        catalog[character] = {}
        for action, asset in preview['characters'][character]['actions'].items():
            frames = []
            # Preview playback uses poses 0..6 and returns to 0 (not column 7).
            for cell in asset['cells'][:7]:
                x, y, width, height = cell['source']
                assert 0 <= x < x + width <= image.width
                assert 0 <= y < y + height <= image.height
                left, top, right, bottom = cell['bounds']
                content = image.crop((x, y, x + width, y + height)).getchannel('A').getbbox()
                assert content is not None, (character, action)
                frames.append({
                    'source': cell['source'], 'anchorX': cell['anchorX'], 'anchorY': cell['anchorY'],
                    'bodyBounds': [left - x, top - y, right - left, bottom - top],
                    'contentTop': content[1], 'visibleHeight': height,
                })
            catalog[character][action] = {
                'image': 'sheet', 'imageSha256': digest(source), 'scale': asset['scale'],
                'frames': frames,
                # Omit the demo's long holds so clicks react immediately.
                'steps': [{'frame': i, 'duration': preview['timings'][action][i]} for i in range(1, 7)]
                         + [{'frame': 0, 'duration': 120}],
            }
    (ASSETS / 'catalog.json').write_text(json.dumps(catalog, indent=2) + '\n', encoding='utf-8')
    print(f"Prepared {len(catalog)} characters / {sum(len(actions) for actions in catalog.values())} actions")


if __name__ == '__main__':
    build_catalog()
