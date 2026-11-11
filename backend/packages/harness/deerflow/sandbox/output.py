"""Bounded process output: decode and redact before retaining any text."""

import codecs
import threading
from collections.abc import Iterable


_SECRET_REDACTION = '[redacted]'
_MIN_MASK_LENGTH = 8


def mask_secret_values(output: str, injected_env: dict[str, str] | None) -> str:
    """Mask known request-scoped secrets, ignoring short commonplace values."""
    if injected_env and output:
        for value in sorted((v for v in injected_env.values() if v and len(v) >= _MIN_MASK_LENGTH), key=len, reverse=True):
            output = output.replace(value, _SECRET_REDACTION)
    return output


class BoundedPipeCapture:
    """Append-only UTF-8 text with bounded lookbehind for split/overlapping secrets.

    A stable prefix and an optional separate rolling tail are retained after redaction.
    Pending raw text is bounded
    by the longest known secret plus one 8192-byte pipe read; a line without a
    newline cannot grow that buffer. Pipes continue to drain after truncation.
    """

    def __init__(self, *, limit_bytes: int = 10 * 1024 * 1024, secret_values: Iterable[str] = (), tail_bytes: int = 0):
        self._limit_bytes = limit_bytes
        self._tail_bytes = tail_bytes
        self._tail = b''
        self._total_characters = 0
        self._secrets = tuple({value for value in secret_values if value and len(value) >= _MIN_MASK_LENGTH})
        self._lookbehind = max((len(value) - 1 for value in self._secrets), default=0)
        self._decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        self._pending = ''
        self._masked = bytearray()
        self._redacting = False
        self._chunks: list[str] = []
        self._kept_bytes = 0
        self._truncated = False
        self._finished = False
        self._lock = threading.Lock()

    def append(self, chunk: bytes) -> None:
        with self._lock:
            if self._finished:
                raise ValueError('Cannot append to finished process output')
            for offset in range(0, len(chunk), 8192):
                if self._truncated and not self._tail_bytes:
                    break
                self._accept(self._decoder.decode(chunk[offset:offset + 8192]), final=False)

    def finish(self) -> None:
        with self._lock:
            if not self._finished and (not self._truncated or self._tail_bytes):
                self._accept(self._decoder.decode(b'', final=True), final=True)
            self._finished = True

    def _accept(self, text: str, *, final: bool) -> None:
        self._pending += text
        self._masked.extend(b'\0' * len(text))
        # Remember coverage in the retained suffix too. This handles overlapping
        # secrets without exposing the tail of a second match across read blocks.
        for secret in self._secrets:
            start = self._pending.find(secret)
            while start >= 0:
                self._masked[start:start + len(secret)] = b'\1' * len(secret)
                start = self._pending.find(secret, start + 1)
        count = len(self._pending) if final else max(0, len(self._pending) - self._lookbehind)
        parts = []
        for char, masked in zip(self._pending[:count], self._masked[:count], strict=True):
            if masked:
                if not self._redacting:
                    parts.append(_SECRET_REDACTION)
            else:
                parts.append(char)
            self._redacting = bool(masked)
        self._pending = self._pending[count:]
        del self._masked[:count]
        data = ''.join(parts).encode('utf-8')
        if self._tail_bytes:
            self._tail = (self._tail + data)[-self._tail_bytes:]
            self._total_characters += len(''.join(parts))
        if self._truncated:
            return
        remaining = self._limit_bytes - self._kept_bytes
        if len(data) > remaining:
            # Only the retention boundary may drop an incomplete code point;
            # malformed bytes emitted by the process still decode as replacement.
            data = data[:remaining].decode('utf-8', errors='ignore').encode('utf-8')
            self._truncated = True
            if not self._tail_bytes:
                self._pending = ''
                self._masked.clear()
                self._decoder.reset()
        if data:
            self._chunks.append(data.decode('utf-8'))
            self._kept_bytes += len(data)

    def read(self, *, complete_lines: bool = False) -> str:
        with self._lock:
            output = ''.join(self._chunks)
            if self._truncated:
                # A fixed append-only marker keeps incremental cursors valid.
                marker = 'latest tail retained separately' if self._tail_bytes else 'remaining output discarded'
                return output + f'\n... [output truncated; {marker}] ...'
            if complete_lines and not self._finished:
                return output[:output.rfind('\n') + 1]
            return output

    def tail(self) -> dict:
        with self._lock:
            text = self._tail.decode('utf-8', errors='ignore')
            return {'text': text, 'omitted_characters': max(0, self._total_characters - len(text))}
