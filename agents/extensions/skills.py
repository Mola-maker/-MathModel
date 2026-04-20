"""Skill loader — markdown snippets with YAML-like frontmatter.

A skill is a small block of instructions that gets injected into the system
prompt when the user message matches a trigger. Think of it as a tiny RAG over
static markdown files — no embeddings, just substring/keyword triggers.

File format (config/skills/<name>.md):

    ---
    name: debug-solver
    description: What to do when the P3 solver fails
    triggers: [solver failed, P3 error, nan, infeasible]
    ---
    When the P3 solver fails:
    1. Check vol/logs/run.log
    2. ...

This format is intentionally simple so it reads like a file, not a config.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
SKILLS_DIR = Path(os.getenv("SKILLS_DIR", BASE_DIR / "config" / "skills"))

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    triggers: tuple[str, ...]
    body: str
    source: str  # "file:path" or "plugin:name"


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Very small YAML-ish parser — supports string, list-inline, int."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    head, body = m.group(1), m.group(2)
    meta: dict = {}
    for line in head.splitlines():
        line = line.rstrip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        if val.startswith("[") and val.endswith("]"):
            inner = val[1:-1]
            meta[key] = tuple(s.strip().strip("'\"") for s in inner.split(",") if s.strip())
        elif val.isdigit():
            meta[key] = int(val)
        else:
            meta[key] = val.strip("'\"")
    return meta, body.strip()


def load_skills(skills_dir: Path | None = None) -> list[Skill]:
    """Load all skills from markdown files under skills_dir."""
    target = skills_dir or SKILLS_DIR
    if not target.exists():
        return []
    out: list[Skill] = []
    for path in sorted(target.glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        meta, body = _parse_frontmatter(text)
        name = meta.get("name") or path.stem
        triggers = meta.get("triggers") or ()
        if isinstance(triggers, str):
            triggers = (triggers,)
        out.append(Skill(
            name=str(name),
            description=str(meta.get("description", "")),
            triggers=tuple(str(t).lower() for t in triggers),
            body=body,
            source=f"file:{path.name}",
        ))
    return out


def match_skills(text: str, skills: list[Skill], limit: int = 3) -> list[Skill]:
    """Return skills whose triggers appear (case-insensitive substring) in text.

    Results are ordered by earliest-triggering skill first. Caps at `limit`
    so we don't balloon the system prompt when many skills match.
    """
    if not text:
        return []
    lowered = text.lower()
    hits: list[Skill] = []
    for sk in skills:
        if any(t and t in lowered for t in sk.triggers):
            hits.append(sk)
        if len(hits) >= limit:
            break
    return hits


def render_skills_block(skills: list[Skill]) -> str:
    """Render matched skills as a prompt-appendable block."""
    if not skills:
        return ""
    parts = ["\n\n## Relevant skills (matched by trigger)"]
    for sk in skills:
        parts.append(f"\n### {sk.name}\n{sk.body}")
    return "\n".join(parts)
