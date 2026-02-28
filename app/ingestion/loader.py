from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class RepoFile:
    repo_path: str
    file_path: str
    absolute_path: str
    language: str
    content: str


def detect_language(path: Path) -> str:
    suffix = path.suffix.lower()
    return {
        ".py": "python",
        ".ts": "typescript",
        ".tsx": "tsx",
        ".js": "javascript",
        ".go": "go",
        ".rs": "rust",
        ".md": "markdown",
        ".json": "json",
        ".yml": "yaml",
        ".yaml": "yaml",
        ".sql": "sql",
    }.get(suffix, "text")


def get_head_commit_sha(repo_path: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()
    except Exception:
        return "unknown"


def should_exclude(path: Path, exclude_globs: list[str]) -> bool:
    normalized = path.as_posix()
    return any(path.match(pattern) or normalized.endswith(pattern.replace("**/", "")) for pattern in exclude_globs)


def load_repo_files(
    repo_path: str,
    include_globs: list[str],
    exclude_globs: list[str],
    max_file_size_kb: int,
) -> list[RepoFile]:
    base = Path(repo_path).resolve()
    files: list[RepoFile] = []

    seen: set[Path] = set()
    for pattern in include_globs:
        for candidate in base.glob(pattern):
            if candidate.is_dir() or candidate in seen:
                continue
            seen.add(candidate)
            rel = candidate.relative_to(base)
            if should_exclude(rel, exclude_globs):
                continue
            if candidate.stat().st_size > max_file_size_kb * 1024:
                continue
            try:
                content = candidate.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            files.append(
                RepoFile(
                    repo_path=str(base),
                    file_path=rel.as_posix(),
                    absolute_path=str(candidate),
                    language=detect_language(candidate),
                    content=content,
                )
            )
    return files
