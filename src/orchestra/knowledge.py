"""Shared knowledge base: a folder of Markdown/text files injected into agent prompts."""

from __future__ import annotations

from pathlib import Path

SUFFIXES = {".md", ".markdown", ".txt"}


class KnowledgeBase:
    def __init__(self, directory: Path | None, max_chars: int = 40_000) -> None:
        self.directory = directory
        self.max_chars = max_chars

    def files(self) -> list[Path]:
        if not self.directory or not self.directory.is_dir():
            return []
        return sorted(
            p for p in self.directory.rglob("*") if p.is_file() and p.suffix.lower() in SUFFIXES
        )

    def listing(self) -> list[dict[str, object]]:
        if not self.directory:
            return []
        return [
            {"path": p.relative_to(self.directory).as_posix(), "bytes": p.stat().st_size}
            for p in self.files()
        ]

    def render(self) -> str:
        """All files concatenated under headings, truncated to ``max_chars``."""
        if not self.directory:
            return ""
        parts: list[str] = []
        used = 0
        for path in self.files():
            text = path.read_text(encoding="utf-8", errors="replace").strip()
            if not text:
                continue
            block = f"### {path.relative_to(self.directory).as_posix()}\n{text}"
            remaining = self.max_chars - used
            if remaining <= 0:
                parts.append("[knowledge truncated]")
                break
            if len(block) > remaining:
                parts.append(block[:remaining] + "\n[knowledge truncated]")
                break
            parts.append(block)
            used += len(block)
        return "\n\n".join(parts)
