"""Text extraction in a short-lived subprocess with a bounded parent timeout."""
import json
import re
import sys
import zipfile
from bisect import bisect_right
from pathlib import Path

MAX_TEXT = 2_000_000


def parse(path: Path, extension: str) -> list[dict]:
    blocks = []
    total = 0

    def append(text, location, line_start=None):
        nonlocal total
        text = text.replace('\x00', '').strip()
        if not text:
            return
        total += len(text)
        if total > MAX_TEXT:
            raise ValueError('正文超过处理上限，请拆分文件后上传')
        block = {'text': text, 'location': location}
        if line_start is not None:
            block['line_start'] = line_start
        blocks.append(block)

    if extension in ('.txt', '.md', '.markdown'):
        raw = path.read_bytes()
        try:
            text = raw.decode('utf-16' if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig')
        except UnicodeDecodeError:
            text = raw.decode('gb18030')
        if '\x00' in text:
            raise ValueError('文件不是有效文本')
        starts = [0] + [match.end() for match in re.finditer(r'\n\s*\n', text)]
        newlines = [match.start() for match in re.finditer('\n', text)]
        for start, paragraph in zip(starts, re.split(r'\n\s*\n', text)):
            trimmed = paragraph.strip()
            first = start + len(paragraph) - len(paragraph.lstrip())
            line = bisect_right(newlines, first) + 1
            append(paragraph, line_location(line, line + trimmed.count('\n')), line)
    elif extension == '.pdf':
        from pypdf import PdfReader
        reader = PdfReader(path)
        if reader.is_encrypted:
            raise ValueError('PDF 已加密，请解除密码后重新上传')
        if len(reader.pages) > 1000:
            raise ValueError('PDF 超过 1000 页，请拆分后上传')
        for index, page in enumerate(reader.pages, 1):
            append(page.extract_text() or '', f'第 {index} 页')
    elif extension == '.docx':
        with zipfile.ZipFile(path) as archive:
            if len(archive.infolist()) > 5000 or sum(item.file_size for item in archive.infolist()) > 100 * 1024 * 1024:
                raise ValueError('DOCX 解压内容过大，请拆分后上传')
            if 'word/document.xml' not in archive.namelist():
                raise ValueError('不是有效的 DOCX 文件')
        from docx import Document
        from docx.table import Table
        document = Document(path)
        for index, item in enumerate(document.iter_inner_content(), 1):
            text = '\n'.join(' | '.join(cell.text for cell in row.cells) for row in item.rows) if isinstance(item, Table) else item.text
            append(text, f'内容块 {index}')
    else:
        raise ValueError('仅支持 TXT、Markdown、PDF、DOCX')
    if not blocks:
        raise ValueError('未提取到文字，无法建立检索索引')
    return blocks


def line_location(start, end):
    return f'第{start}行' if start == end else f'第{start}行-第{end}行'


def compact_locations(locations):
    """Merge adjacent source ranges without pretending gaps are cited."""
    groups = {}
    other = []
    for location in locations:
        for label in location.split('、'):
            match = re.fullmatch(r'第\s*(\d+)\s*(行|页)(?:-第\s*(\d+)\s*\2)?', label)
            if match:
                groups.setdefault(match[2], []).append((int(match[1]), int(match[3] or match[1])))
            else:
                other.append(label)
    output = []
    for unit, ranges in groups.items():
        merged = []
        for start, end in sorted(ranges):
            if merged and start <= merged[-1][1] + 1:
                merged[-1][1] = max(end, merged[-1][1])
            else:
                merged.append([start, end])
        output.extend(f'第{start}{unit}' if start == end else f'第{start}{unit}-第{end}{unit}' for start, end in merged)
    return '、'.join(output + list(dict.fromkeys(other)))


def chunks(blocks: list[dict], size=1800, overlap=180) -> list[dict]:
    """Keep adjacent short blocks together; split oversized blocks with overlap."""
    def chunk_location(positions):
        # A chunk contains consecutive original blocks, including the blank
        # lines between them. Cite that span, not every separate paragraph.
        if all(re.fullmatch(r'第\d+行(?:-第\d+行)?', value) for value in positions):
            first = int(re.findall(r'\d+', positions[0])[0])
            last = int(re.findall(r'\d+', positions[-1])[-1])
            return line_location(first, last)
        return compact_locations(positions)

    output, text, positions = [], '', []
    for block in blocks:
        if text and len(text) + len(block['text']) + 2 > size:
            output.append({'text': text, 'location': chunk_location(positions)})
            text, positions = '', []
        content = block['text']
        offset = 0
        def position(segment):
            if 'line_start' not in block:
                return block['location']
            start = block['line_start'] + block['text'][:offset].count('\n')
            return line_location(start, start + segment.rstrip().count('\n'))
        while len(content) > size:
            end = max(content.rfind('\n', size // 2, size), content.rfind('。', size // 2, size))
            end = end + 1 if end > 0 else size
            output.append({'text': content[:end], 'location': position(content[:end])})
            advance = max(1, end - overlap)
            offset += advance
            content = content[advance:]
        text = f'{text}\n\n{content}'.strip()
        positions.append(position(content))
    if text:
        output.append({'text': text, 'location': chunk_location(positions)})
    return output


if __name__ == '__main__':
    try:
        print(json.dumps({'blocks': parse(Path(sys.argv[1]), sys.argv[2])}, ensure_ascii=True))
    except Exception as error:
        print(json.dumps({'error': str(error) if isinstance(error, ValueError) else '文件解析失败，请确认格式正确且文件未损坏'}))
