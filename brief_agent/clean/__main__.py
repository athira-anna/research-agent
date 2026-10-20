"""Run stage 2 on its own:  python -m brief_agent.clean --run-dir runs/<id> --topic "..." """

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from ..llm import GeminiLLM
from .pipeline import clean_sources


def main() -> None:
    p = argparse.ArgumentParser(prog="python -m brief_agent.clean", description=__doc__)
    p.add_argument("--run-dir", type=Path, required=True, help="a stage 1 run folder (has sources_raw.json)")
    p.add_argument("--topic", required=True)
    p.add_argument("--no-llm", action="store_true", help="skip the relevance LLM call (keyword fallback)")
    args = p.parse_args()

    sources = asyncio.run(clean_sources(args.run_dir, args.topic, llm=None if args.no_llm else GeminiLLM()))

    for s in sources:
        rel = f"{s.relevance.label}/{s.relevance.page_kind or '?'}" + ("" if s.relevance.checked else "*") \
            if s.relevance else "-"
        kept = f"{len(s.selected_ids)}/{len(s.blocks)} blocks" if s.blocks else ""
        print(f"\n{s.source_id}  fetch {s.fetch['status']:<14} processing {s.processing.status:<8} "
              f"{s.usability.status:<9} {rel:<28} {kept}")
        print(f"      {(s.meta.get('title') or s.url)[:80]}  [{s.meta.get('published_date') or 'no date'}]")
        for r in s.usability.reasons:
            print(f"      reason: {r}")
        for n in s.usability.notes:
            print(f"      note:   {n}")
    if any(s.relevance and not s.relevance.checked for s in sources):
        print("\n* relevance from keyword fallback (LLM not used or failed)")
    print(f"\nWrote {args.run_dir / 'sources.json'} and {args.run_dir / 'clean/'}")


if __name__ == "__main__":
    main()
