#!/usr/bin/env python3
"""Export prompts from a CC Switch SQLite database into Markdown files."""

from __future__ import annotations

import argparse
import re
import sqlite3
from pathlib import Path


DEFAULT_NAME_MAP = {
    "指标业务定义": "metrics-business-definition.md",
    "AskUserQuest&Todolist": "ask-user-question-and-todolist.md",
    "问问题创建代办-简版": "question-to-todo-lite.md",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export CC Switch prompts into Markdown files."
    )
    parser.add_argument(
        "--db",
        default=str(Path.home() / ".cc-switch" / "cc-switch.db"),
        help="Path to cc-switch.db",
    )
    parser.add_argument(
        "--app",
        default="claude",
        help="Prompt app_type to export, e.g. claude/codex/openclaw",
    )
    parser.add_argument(
        "--output-dir",
        default="prompts",
        help="Repository directory for exported Markdown files",
    )
    parser.add_argument(
        "--name",
        action="append",
        dest="names",
        help="Prompt name to export. Repeat to export a subset.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned writes without touching files",
    )
    return parser.parse_args()


def slugify(name: str, index: int) -> str:
    if name in DEFAULT_NAME_MAP:
        return DEFAULT_NAME_MAP[name]

    lowered = name.lower().strip()
    lowered = lowered.replace("&", " and ")
    lowered = re.sub(r"[^a-z0-9]+", "-", lowered)
    lowered = re.sub(r"-{2,}", "-", lowered).strip("-")
    if lowered:
        return f"{lowered}.md"
    return f"prompt-{index:02d}.md"


def fetch_prompts(db_path: Path, app_type: str) -> list[tuple[str, str]]:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT name, content
            FROM prompts
            WHERE app_type = ?
            ORDER BY created_at ASC, id ASC
            """,
            (app_type,),
        ).fetchall()
    finally:
        conn.close()
    return [(str(name), str(content)) for name, content in rows]


def main() -> int:
    args = parse_args()
    db_path = Path(args.db).expanduser()
    output_dir = Path(args.output_dir)

    if not db_path.exists():
        raise SystemExit(f"Database not found: {db_path}")

    prompts = fetch_prompts(db_path, args.app)
    if args.names:
        wanted = set(args.names)
        prompts = [(name, content) for name, content in prompts if name in wanted]

    if not prompts:
        raise SystemExit("No prompts matched the selection.")

    output_dir.mkdir(parents=True, exist_ok=True)

    for index, (name, content) in enumerate(prompts, start=1):
        filename = slugify(name, index)
        target = output_dir / filename
        body = f"# {name}\n\n{content.rstrip()}\n"
        if args.dry_run:
            print(f"[dry-run] {target}")
            continue
        target.write_text(body, encoding="utf-8")
        print(f"Wrote {target}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
