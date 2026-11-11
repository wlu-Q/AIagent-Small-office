import fnmatch
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

IGNORE_PATTERNS = [
    ".git",
    ".office-worktrees",
    ".svn",
    ".hg",
    ".bzr",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".env",
    "env",
    ".tox",
    ".nox",
    ".eggs",
    "*.egg-info",
    "site-packages",
    "dist",
    "build",
    ".next",
    ".nuxt",
    ".output",
    ".turbo",
    "target",
    "out",
    ".idea",
    ".vscode",
    "*.swp",
    "*.swo",
    "*~",
    ".project",
    ".classpath",
    ".settings",
    ".DS_Store",
    "Thumbs.db",
    "desktop.ini",
    "*.lnk",
    "*.log",
    "*.tmp",
    "*.temp",
    ".upload-*.part",
    "*.bak",
    "*.cache",
    ".cache",
    "logs",
    ".coverage",
    "coverage",
    ".nyc_output",
    "htmlcov",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
]

DEFAULT_MAX_FILE_SIZE_BYTES = 1_000_000
DEFAULT_LINE_SUMMARY_LENGTH = 200


@dataclass(frozen=True)
class GrepMatch:
    path: str
    line_number: int
    line: str


@dataclass
class GrepResult:
    matches: list[GrepMatch]
    truncated: bool = False
    skipped_long_lines: int = 0
    skipped_files: int = 0


# ``should_ignore_name`` runs once per directory entry during glob/grep tree
# walks, so we avoid ~50 ``fnmatch`` calls per name. Most ignore patterns are
# literal names (O(1) set lookup after normcase); the few glob patterns are
# pre-translated into a single combined regex. ``os.path.normcase`` keeps the
# same case behavior ``fnmatch`` applies (case-sensitive on POSIX, folded on
# Windows).
_EXACT_IGNORE_NAMES = frozenset(os.path.normcase(p) for p in IGNORE_PATTERNS if not any(c in p for c in "*?["))
_GLOB_IGNORE_PATTERNS = [p for p in IGNORE_PATTERNS if any(c in p for c in "*?[")]
_GLOB_IGNORE_RE = re.compile("|".join(fnmatch.translate(os.path.normcase(p)) for p in _GLOB_IGNORE_PATTERNS)) if _GLOB_IGNORE_PATTERNS else None


def should_ignore_name(name: str) -> bool:
    normalized = os.path.normcase(name)
    if normalized in _EXACT_IGNORE_NAMES:
        return True
    return _GLOB_IGNORE_RE is not None and _GLOB_IGNORE_RE.match(normalized) is not None


def should_ignore_path(path: str) -> bool:
    return any(should_ignore_name(segment) for segment in path.replace("\\", "/").split("/") if segment)


def path_matches(pattern: str, rel_path: str) -> bool:
    path = PurePosixPath(rel_path)
    if path.match(pattern):
        return True
    if pattern.startswith("**/"):
        return path.match(pattern[3:])
    return False


def truncate_line(line: str, max_chars: int = DEFAULT_LINE_SUMMARY_LENGTH) -> str:
    line = line.rstrip("\n\r")
    if len(line) <= max_chars:
        return line
    return line[: max_chars - 3] + "..."


def is_binary_file(path: Path, sample_size: int = 8192) -> bool:
    with path.open("rb") as handle:
        return b"\0" in handle.read(sample_size)


def find_glob_matches(root: Path, pattern: str, *, include_dirs: bool = False, max_results: int = 200, path_filter: Callable[[Path], bool] | None = None) -> tuple[list[str], bool]:
    matches: list[str] = []
    truncated = False
    root = root.resolve()

    if not root.exists():
        raise FileNotFoundError(root)
    if not root.is_dir():
        raise NotADirectoryError(root)

    for current_root, dirs, files in os.walk(root):
        dirs[:] = [name for name in dirs if not should_ignore_name(name)]
        # root is already resolved; os.walk builds current_root by joining under root,
        # so relative_to() works without an extra stat()/resolve() per directory.
        rel_dir = Path(current_root).relative_to(root)

        if include_dirs:
            for name in dirs:
                if path_filter is not None and not path_filter(Path(current_root) / name):
                    continue
                rel_path = (rel_dir / name).as_posix()
                if path_matches(pattern, rel_path):
                    matches.append(str(Path(current_root) / name))
                    if len(matches) >= max_results:
                        truncated = True
                        return matches, truncated

        for name in files:
            if should_ignore_name(name):
                continue
            if path_filter is not None and not path_filter(Path(current_root) / name):
                continue
            rel_path = (rel_dir / name).as_posix()
            if path_matches(pattern, rel_path):
                matches.append(str(Path(current_root) / name))
                if len(matches) >= max_results:
                    truncated = True
                    return matches, truncated

    return matches, truncated


def find_grep_matches(
    root: Path,
    pattern: str,
    *,
    glob_pattern: str | None = None,
    literal: bool = False,
    case_sensitive: bool = False,
    max_results: int = 100,
    offset: int = 0,
    max_file_size: int = DEFAULT_MAX_FILE_SIZE_BYTES,
    line_summary_length: int = DEFAULT_LINE_SUMMARY_LENGTH,
    path_filter: Callable[[Path], bool] | None = None,
) -> GrepResult:
    if max_results < 1 or offset < 0:
        raise ValueError('max_results must be positive and offset must be non-negative')
    result = GrepResult([])
    matched_count = 0
    root = root.resolve()

    if not root.exists():
        raise FileNotFoundError(root)
    if not root.is_dir():
        raise NotADirectoryError(root)

    flags = 0 if case_sensitive else re.IGNORECASE
    regex = None if literal else re.compile(pattern, flags)
    needle = pattern if case_sensitive else pattern.lower()

    # Skip lines longer than this to prevent ReDoS on minified / no-newline files.
    _max_line_chars = line_summary_length * 10

    for current_root, dirs, files in os.walk(root):
        dirs[:] = sorted(name for name in dirs if not should_ignore_name(name))
        rel_dir = Path(current_root).relative_to(root)

        for name in sorted(files):
            if should_ignore_name(name):
                continue

            candidate_path = Path(current_root) / name
            if path_filter is not None and not path_filter(candidate_path):
                continue
            rel_path = (rel_dir / name).as_posix()

            if glob_pattern is not None and not path_matches(glob_pattern, rel_path):
                continue

            try:
                if candidate_path.is_symlink():
                    continue
                file_path = candidate_path.resolve()
                if not file_path.is_relative_to(root):
                    continue
                if file_path.stat().st_size > max_file_size:
                    result.skipped_files += 1
                    continue
                if is_binary_file(file_path):
                    continue
                with file_path.open(encoding="utf-8", errors="replace") as handle:
                    for line_number, line in enumerate(handle, start=1):
                        if not literal and len(line) > _max_line_chars:
                            result.skipped_long_lines += 1
                            continue
                        if literal:
                            position = (line if case_sensitive else line.lower()).find(needle)
                        else:
                            match = regex.search(line)
                            position = match.start() if match else -1
                        if position >= 0:
                            matched_count += 1
                            if matched_count <= offset:
                                continue
                            if len(result.matches) == max_results:
                                result.truncated = True
                                return result
                            start = max(0, position - line_summary_length // 3)
                            summary = ('...' if start else '') + truncate_line(line[start:], line_summary_length)
                            result.matches.append(
                                GrepMatch(
                                    path=str(file_path),
                                    line_number=line_number,
                                    line=summary,
                                )
                            )
            except OSError:
                result.skipped_files += 1
                continue

    return result
