"""Run stage 1 on its own:  python -m brief_agent.fetch URL [URL ...]"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
from pathlib import Path

from .fetcher import FetchConfig, fetch_sources


def main() -> None:
    p = argparse.ArgumentParser(prog="python -m brief_agent.fetch", description=__doc__)
    p.add_argument("urls", nargs="*", help="source URLs")
    p.add_argument("--file", type=Path, help="text file with one URL per line")
    p.add_argument("--run-dir", type=Path, help="where to write output (default: runs/<timestamp>)")
    p.add_argument("--refresh", action="store_true", help="ignore the cache and fetch again")
    p.add_argument("--no-cache", action="store_true", help="don't read or write the cache")
    p.add_argument("--no-external", action="store_true",
                   help="don't send URLs to third-party services (reader, archive)")
    args = p.parse_args()

    urls = list(args.urls)
    if args.file:
        urls += [line.strip() for line in args.file.read_text().splitlines()
                 if line.strip() and not line.startswith("#")]
    if not urls:
        p.error("give at least one URL")

    run_dir = args.run_dir or Path("runs") / datetime.now().strftime("%Y%m%d-%H%M%S")
    config = FetchConfig(allow_external=not args.no_external, use_cache=not args.no_cache, refresh=args.refresh)
    records = asyncio.run(fetch_sources(urls, run_dir, config))

    for r in records:
        status = r.status.value + (f" ({r.status_detail})" if r.status_detail else "")
        via = f"via {r.method}" + (" [cached]" if r.from_cache else "") if r.method else ""
        print(f"\n{r.source_id}  {status:<28} {via:<22} {r.text_preview_chars:>7,} chars  {r.url}")
        for a in r.attempts:
            print(f"      {a.method:<8} → {a.result:<16} {a.seconds:>6.1f}s  {a.reason}")
    print(f"\nWrote {run_dir / 'sources_raw.json'}")


if __name__ == "__main__":
    main()
