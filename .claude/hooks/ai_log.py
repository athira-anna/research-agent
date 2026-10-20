#!/usr/bin/env python3
"""Claude Code hook: keeps an automatic record of how this project was built with AI.

Modes (passed as argv[1]):
  prompt  - UserPromptSubmit: append the user's prompt to ai-log/prompt_log.md
  stop    - Stop: export the session transcript to ai-log/transcripts/ (raw .jsonl + readable .md)

Hooks must never block the session, so every failure is swallowed and we always exit 0.
"""
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path


def project_dir(payload):
    return Path(os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or ".")


def log_prompt(payload):
    prompt = (payload.get("prompt") or "").strip()
    if not prompt:
        return
    log = project_dir(payload) / "ai-log" / "prompt_log.md"
    log.parent.mkdir(parents=True, exist_ok=True)
    new_file = not log.exists()
    with log.open("a", encoding="utf-8") as f:
        if new_file:
            f.write("# Raw prompt log\n\n"
                    "Every prompt sent to Claude Code in this project, captured automatically "
                    "by a `UserPromptSubmit` hook. Curated highlights live in `prompts.md`.\n\n")
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        session = (payload.get("session_id") or "unknown")[:8]
        f.write(f"---\n\n### {ts} · session `{session}`\n\n````text\n{prompt}\n````\n\n")


def _text_blocks(content):
    """Yield (kind, text) pairs from a message's content (string or list of blocks)."""
    if isinstance(content, str):
        yield "text", content
        return
    for block in content or []:
        kind = block.get("type")
        if kind == "text":
            yield "text", block.get("text", "")
        elif kind == "tool_use":
            args = json.dumps(block.get("input", {}), ensure_ascii=False)
            yield "tool", f"`{block.get('name')}` {args[:300]}{'…' if len(args) > 300 else ''}"


def render_markdown(jsonl_path):
    lines = ["# Session transcript\n",
             f"_Exported automatically from `{jsonl_path.name}`. "
             "Tool results and model thinking are omitted for readability; see the .jsonl for everything._\n"]
    for raw in jsonl_path.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        role = entry.get("type")
        if role not in ("user", "assistant") or entry.get("isMeta"):
            continue
        for kind, text in _text_blocks((entry.get("message") or {}).get("content")):
            text = text.strip()
            # Skip harness-injected wrappers (system reminders, command plumbing).
            if not text or text.startswith("<system-reminder>") or text.startswith("<command-"):
                continue
            if kind == "tool":
                lines.append(f"> 🔧 {text}\n")
            elif role == "user":
                lines.append(f"\n## 🧑 User\n\n{text}\n")
            else:
                lines.append(f"\n## 🤖 Claude\n\n{text}\n")
    return "\n".join(lines)


def export_transcript(payload):
    src = payload.get("transcript_path")
    if not src or not Path(src).exists():
        return
    src = Path(src)
    out = project_dir(payload) / "ai-log" / "transcripts"
    out.mkdir(parents=True, exist_ok=True)
    name = (payload.get("session_id") or src.stem)
    shutil.copyfile(src, out / f"{name}.jsonl")
    (out / f"{name}.md").write_text(render_markdown(src), encoding="utf-8")


def main():
    try:
        payload = json.load(sys.stdin)
        mode = sys.argv[1] if len(sys.argv) > 1 else "prompt"
        if mode == "prompt":
            log_prompt(payload)
        elif mode == "stop":
            export_transcript(payload)
    except Exception as exc:  # never break the session over logging
        print(f"ai_log hook error: {exc}", file=sys.stderr)
    sys.exit(0)


if __name__ == "__main__":
    main()
