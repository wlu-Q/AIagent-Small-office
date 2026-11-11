"""Register imagegen daily poses in the legacy 52-frame order using FFmpeg.

Image analysis only locates sprites. No body parts are reconstructed. The front
idle pose comes directly from the accepted reaction atlas, not a new drawing.
"""
import hashlib
import json
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

CLIENT = Path(__file__).resolve().parents[1]
ROOT = CLIENT / 'public/assets/character'
SOURCE = CLIENT / 'assets-source/employee-daily'
WORK = CLIENT / 'output/employee-daily'
CATALOG = json.loads((ROOT / 'reactions/catalog.json').read_text(encoding='utf-8'))


def ffmpeg(*args):
    subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', *map(str, args)], check=True)


def prepare(name):
    path = SOURCE / f'{name}.png'
    sheet = Image.open(path).convert('RGBA')
    alpha = np.array(sheet.getchannel('A'))
    count, labels, stats, _ = cv2.connectedComponentsWithStats((alpha >= 128).astype('uint8'), 8)
    cells = {}
    for label in range(1, count):
        x, y, w, h, area = map(int, stats[label])
        if area < 1000:
            continue
        key = (min(3, int((y + h / 2) * 4 / sheet.height)), min(7, int((x + w / 2) * 8 / sheet.width)))
        if key not in cells or area > stats[cells[key], 4]:
            cells[key] = label
    assert len(cells) == 32, (name, 'expected 32 poses', len(cells))
    reference = CATALOG[name]['nod']
    ref = reference['frames'][0]
    target_height = ref['bodyBounds'][3] * reference['scale']
    scale = target_height * 4 / int(stats[cells[(0, 0)], 3])
    out = WORK / name
    out.mkdir(parents=True, exist_ok=True)
    records = []

    for row in range(4):
        nx, ny, nw, nh, _ = map(int, stats[cells[(row, 0)]])
        neutral_head = np.where(labels[ny:ny + int(nh * .55), nx:nx + nw] == cells[(row, 0)])[1]
        head_offset = nw / 2 - (int(neutral_head.min()) + int(neutral_head.max()) + 1) / 2
        for col in range(8):
            idx = cells[(row, col)]
            x, y, w, h, _ = map(int, stats[idx])
            head = np.where(labels[y:y + int(h * .55), x:x + w] == idx)[1]
            anchor_x = (int(head.min()) + int(head.max()) + 1) / 2 + head_offset
            # A single scale per character preserves head size in sitting poses.
            size = [round(w * scale), round(h * scale)]
            left = round(96 - anchor_x * scale)
            top = 224 - size[1]
            assert left >= 0 and left + size[0] <= 192 and top >= 0, (name, row, col, size)
            ffmpeg('-i', path, '-vf', f'crop={w}:{h}:{x}:{y},scale={size[0]}:{size[1]}:flags=neighbor,pad=192:228:{left}:{top}:color=black@0',
                   '-frames:v', 1, out / f'pose-{row}-{col}.png')
            records.append({'row': row, 'column': col, 'source': [x, y, w, h], 'scale': scale / 4})

    # Use precisely the accepted front neutral pose, including its anchor.
    sx, sy, sw, sh = ref['source']
    # Detached hair tufts belong to the identity too; bodyBounds is only for
    # outlining the main connected component, not for cropping the artwork.
    reference_path = ROOT / f"reactions/{name}/{reference['image']}.png"
    alpha_bounds = Image.open(reference_path).convert('RGBA').crop((sx, sy, sx + sw, sy + sh)).getchannel('A').getbbox()
    bx, by, right, bottom = alpha_bounds
    bw, bh = right - bx, bottom - by
    factor = reference['scale'] * 4
    rw, rh = round(bw * factor), round(bh * factor)
    rx = round(96 - (ref['anchorX'] - bx) * factor)
    ry = round(224 - (ref['anchorY'] - by) * factor)
    ffmpeg('-i', reference_path, '-vf',
           f'crop={bw}:{bh}:{sx + bx}:{sy + by},scale={rw}:{rh}:flags=neighbor,pad=192:228:{rx}:{ry}:color=black@0',
           '-frames:v', 1, out / 'pose-0-0.png')
    # Leave a transparent gutter below the foot anchor. Without this, rounding
    # an antialiased edge to 1px beyond the canvas makes FFmpeg auto-center it.
    assert ry >= 0 and ry + rh <= 228 and rx >= 0 and rx + rw <= 192
    packed_bounds = Image.open(out / 'pose-0-0.png').getchannel('A').getbbox()
    assert abs(packed_bounds[1] - ry) <= 1 and abs(packed_bounds[3] - (ry + rh)) <= 1, (name, 'front pose shifted')
    for frame_index, (row, column) in enumerate((row, column) for row in [1, 2, 3, 0] for column in range(8)):
        shutil.copyfile(out / f'pose-{row}-{column}.png', out / f'compact-{frame_index:02}.png')
    ffmpeg('-i', out / 'compact-%02d.png', '-vf', 'scale=96:114:flags=neighbor,tile=8x4:nb_frames=32:color=black@0', '-frames:v', 1, ROOT / f'{name}.png')
    if name == 'ethan':
        subprocess.run(['node', str(CLIENT / 'scripts/prepare-ethan-stride.cjs')], check=True)
    avatar = ROOT / 'portraits' / f'{name}.png'
    avatar.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg('-i', out / 'pose-0-0.png', '-vf', 'crop=144:144:24:32', '-frames:v', 1, avatar)
    return name, {'sourceSha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                  'atlasSha256': hashlib.sha256((ROOT / f'{name}.png').read_bytes()).hexdigest(),
                  'height': target_height, 'poses': records}


if __name__ == '__main__':
    WORK.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=3) as pool:
        result = dict(pool.map(prepare, CATALOG))
    (SOURCE / 'frames.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(f'Prepared {len(result)} daily sheets, each with 32 unique poses and a matching portrait')
