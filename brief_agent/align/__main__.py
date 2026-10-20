"""Run stage 4 on its own:  python -m brief_agent.align --run-dir runs/<id>"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from ..llm import GeminiLLM
from .aligner import align


def main() -> None:
    p = argparse.ArgumentParser(prog="python -m brief_agent.align", description=__doc__)
    p.add_argument("--run-dir", type=Path, required=True)
    p.add_argument("--no-llm", action="store_true", help="code-only fallback clustering")
    args = p.parse_args()
    out = asyncio.run(align(args.run_dir, None if args.no_llm else GeminiLLM()))
    for qid, q in out["questions"].items():
        print(f"\n{qid}  [{q['status']}{' · ' + q['model'] if q['model'] else ''}]")
        for cl in q["clusters"]:
            print(f"  {cl['cluster_id']}  {cl['label']}")
            for cid in cl["claim_ids"]:
                c = out["claims"][cid]
                sc = c["canonical_scope"]
                print(f"      {cid:<9} {c['label'][:28]:<28} pop={sc['population']} geo={sc['geography']} time={sc['timeframe']}")
        for f in q["fixes"]:
            print(f"  fix: {f}")
    print(f"\nWrote {args.run_dir / 'alignment.json'}")


if __name__ == "__main__":
    main()
