"""Process-output boundaries, independent of shell/platform timing."""

import pytest

from deerflow.sandbox.output import BoundedPipeCapture


@pytest.mark.parametrize('block_size', [1, 2, 7, 8192])
def test_split_utf8_and_secret_never_rewrite_published_output(block_size):
    secret = 'test-secret-中文-123456789'
    text = f'开始\n{secret}\n结束，没有换行'
    capture = BoundedPipeCapture(secret_values=[secret])
    raw = text.encode('utf-8')
    published = ''
    for offset in range(0, len(raw), block_size):
        capture.append(raw[offset:offset + block_size])
        current = capture.read()
        assert current.startswith(published)
        assert 'test-secret' not in current and '\ufffd' not in current
        published = current
    capture.finish()
    assert capture.read() == '开始\n[redacted]\n结束，没有换行'
    assert capture.read().startswith(published)


def test_overlapping_and_multiline_secrets_keep_coverage_across_blocks():
    capture = BoundedPipeCapture(secret_values=['abcdefghijk', 'hijkLMNOPQ', 'line-one\nline-two'])
    for byte in b'prefix abcdefghijkLMNOPQ line-one\nline-two suffix':
        capture.append(bytes([byte]))
        assert 'abcdefgh' not in capture.read() and 'LMNOPQ' not in capture.read()
    capture.finish()
    assert capture.read() == 'prefix [redacted] [redacted] suffix'


@pytest.mark.parametrize('remaining', [1, 2, 3])
def test_retention_limit_preserves_utf8_and_marker_is_stable(remaining):
    capture = BoundedPipeCapture(limit_bytes=16)
    capture.append(('x' * (16 - remaining) + '中文').encode('utf-8'))
    first = capture.read()
    assert '\ufffd' not in first and 'truncated' in first
    assert len(first.split('\n...')[0].encode('utf-8')) <= 16
    for _ in range(5):
        capture.append(b'z' * 100000)
        assert capture.read() == first
    capture.finish()
    assert capture.read() == first


def test_secret_is_masked_before_limit_and_pending_buffer_is_bounded():
    secret = 'acceptance-secret-ONLY-FAKE-1234567890'
    capture = BoundedPipeCapture(limit_bytes=64, secret_values=[secret])
    for byte in ('x' * 44 + secret + 'y' * 400).encode():
        capture.append(bytes([byte]))
        assert len(capture._pending) <= len(secret) - 1
        assert capture._kept_bytes <= 64
        assert 'acceptance-secret' not in capture.read()
    capture.finish()
    assert capture.read().startswith('x' * 44 + '[redacted]' + 'y' * 10)
    assert 'truncated' in capture.read()


def test_long_line_makes_progress_without_unbounded_line_buffer():
    capture = BoundedPipeCapture(limit_bytes=100000, secret_values=['secret-of-length-20!'])
    capture.append(b'x' * 90000)
    assert len(capture.read()) > 89000
    assert len(capture._pending) < 20
    assert capture.read(complete_lines=True) == ''
    capture.finish()
    assert capture.read(complete_lines=True) == 'x' * 90000


def test_unfinished_codepoint_waits_until_next_block():
    capture = BoundedPipeCapture()
    raw = '中'.encode('utf-8')
    capture.append(raw[:2])
    assert capture.read() == ''
    capture.append(raw[2:])
    assert capture.read() == '中'
    capture.finish()
    assert capture.read() == '中'


@pytest.mark.parametrize('block_size', [1, 7, 8192])
def test_rolling_tail_retains_final_error_without_changing_prefix(block_size):
    secret = 'tail-secret-中文-123456789'
    capture = BoundedPipeCapture(limit_bytes=64, tail_bytes=128, secret_values=[secret])
    text = 'x' * 500 + f'\n{secret}\n最后的错误，没有换行'
    raw = text.encode('utf-8')
    prefix = ''
    for offset in range(0, len(raw), block_size):
        capture.append(raw[offset:offset + block_size])
        current = capture.read()
        assert current.startswith(prefix)
        assert 'tail-secret' not in capture.tail()['text']
        assert len(capture._tail) <= 128
        prefix = current
    capture.finish()
    tail = capture.tail()
    assert capture.read().startswith(prefix)
    assert tail['text'].endswith('[redacted]\n最后的错误，没有换行')
    assert '\ufffd' not in tail['text']
    assert tail['omitted_characters'] == len(text.replace(secret, '[redacted]')) - len(tail['text'])


def test_tail_does_not_expose_overlapping_secrets_after_prefix_limit():
    capture = BoundedPipeCapture(limit_bytes=8, tail_bytes=64, secret_values=['abcdefghijk', 'hijkLMNOPQ'])
    capture.append(b'x' * 100)
    for byte in b'abcdefghijkLMNOPQ final':
        capture.append(bytes([byte]))
        assert 'abcdefgh' not in capture.tail()['text']
        assert 'LMNOPQ' not in capture.tail()['text']
    capture.finish()
    assert capture.tail()['text'].endswith('[redacted] final')
