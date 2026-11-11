"""Losslessly pack the approved GIF frames; Pillow only inspects/validates pixels.

Run from the repository root: python client/scripts/prepare-employee-reactions.py
ffmpeg performs GIF decoding and PNG atlas packing. No poses are redrawn.
"""
from pathlib import Path
from PIL import Image, ImageSequence
import hashlib
import json
import subprocess

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'client/public/assets/character/reactions/ethan'
OUT.mkdir(parents=True, exist_ok=True)
SOURCES = {
    action: ROOT / 'output/imagegen/ethan-actions-20261106-elastic' / f'ethan-{action}-elastic.gif'
    for action in ['nod', 'scratch', 'clap', 'stretch']
}
SOURCES['jump'] = ROOT / 'output/imagegen/ethan-click-jump-20261106-v2/ethan-click-jump-v2.gif'


def body_bounds(image, action):
    width, height = image.size
    pixels = image.load()
    seen = set()
    largest = []
    # The jump preview has a separate ground shadow below row 370.
    limit = 370 if action == 'jump' else height
    for y in range(limit):
        for x in range(width):
            if (x, y) in seen or pixels[x, y][3] < 128:
                continue
            component = [(x, y)]
            seen.add((x, y))
            for px, py in component:
                for nx, ny in [(px - 1, py), (px + 1, py), (px, py - 1), (px, py + 1)]:
                    if 0 <= nx < width and 0 <= ny < limit and (nx, ny) not in seen and pixels[nx, ny][3] >= 128:
                        seen.add((nx, ny))
                        component.append((nx, ny))
            if len(component) > len(largest):
                largest = component
    xs, ys = zip(*largest)
    return [min(xs), min(ys), max(xs) - min(xs) + 1, max(ys) - min(ys) + 1]


def canonical(image):
    # Invisible RGB values can differ between GIF decoders, and do not affect rendering.
    data = bytearray(image.convert('RGBA').tobytes())
    for i in range(0, len(data), 4):
        if data[i + 3] == 0:
            data[i:i + 3] = b'\x00\x00\x00'
    return bytes(data)


manifest = {}
for action, source in SOURCES.items():
    gif = Image.open(source)
    frames = [frame.convert('RGBA').copy() for frame in ImageSequence.Iterator(gif)]
    gif.seek(0)
    durations = [frame.info['duration'] for frame in ImageSequence.Iterator(gif)]
    width, height = gif.size
    rows = (len(frames) + 3) // 4
    destination = OUT / f'{action}.png'
    subprocess.run([
        'ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(source),
        '-vf', f'format=rgba,tile=4x{rows}:color=black@0', '-frames:v', '1', str(destination),
    ], check=True)
    atlas = Image.open(destination).convert('RGBA')
    hashes = []
    for index, frame in enumerate(frames):
        x, y = index % 4 * width, index // 4 * height
        packed = atlas.crop((x, y, x + width, y + height))
        assert canonical(packed) == canonical(frame), (action, index, 'GIF pixels changed')
        hashes.append(hashlib.sha256(canonical(frame)).hexdigest())
    bounds = [body_bounds(frame, action) for frame in frames]
    # Omit only the GIF's opening/closing demo pauses, retain every gesture pose.
    motion = list(range(1, len(frames) - 2))
    original_fps = len(motion) * 1000 / sum(durations[i] for i in motion)
    target_fps = original_fps - (1 if original_fps < 7 else 2)
    ratio = original_fps / target_fps
    steps = [{'frame': i, 'duration': round(durations[i] * ratio)} for i in motion]
    steps.append({'frame': 0, 'duration': 120})
    manifest[action] = {
        'source': source.relative_to(ROOT).as_posix(),
        'sourceSha256': hashlib.sha256(source.read_bytes()).hexdigest(),
        'frameWidth': width, 'frameHeight': height, 'frameCount': len(frames), 'columns': 4,
        'anchorX': width / 2, 'anchorY': bounds[0][1] + bounds[0][3],
        'scale': 46 / bounds[0][3], 'bodyBounds': bounds,
        'contentTop': [frame.getchannel('A').getbbox()[1] for frame in frames],
        'frameHashes': hashes,
        'sourceFps': original_fps, 'playbackFps': target_fps, 'steps': steps,
    }
    print(action, len(frames), 'lossless frames;', round(original_fps, 2), '->', round(target_fps, 2), 'fps')
(OUT / 'frames.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
