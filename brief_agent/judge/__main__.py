"""Run stage 5 on its own:  python -m brief_agent.judge --run-dir runs/<id>   (needs alignment.json)"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from ..llm import GeminiLLM
from .pipeline import judge


def main() -> None:
    p = argparse.ArgumentParser(prog="python -m brief_agent.judge", description=__doc__)
    p.add_argument("--run-dir", type=Path, required=True)
    p.add_argument("--no-llm", action="store_true", help="code-decidable labels only")
    args = p.parse_args()
    a = asyncio.run(judge(args.run_dir, None if args.no_llm else GeminiLLM()))
    for f in a["findings"] + a["emerging"]:
        v = f["verdict"]
        extra = {k: v[k] for k in ("balance", "patterns", "complementary_slices", "reasons", "contradiction_types") if v.get(k)}
        print(f"\n{f['cluster_id']}  {v['label'].upper()} ({f['strength']['level']}, {v.get('origins')} origins / "
              f"{v.get('sources')} sources) {extra or ''}")
        print(f"   {f['label']}")
        if f["llm_summary"]:
            print(f"   llm summary: {f['llm_summary']}")
        for r in f["likely_reasons"][:6]:
            print(f"   why: {r}")
        for c in f["claims"]:
            val = f" [{c['value']['low']:g} {c['value']['unit']}]" if c["value"] else ""
            print(f"     {c['claim_id']:<9} {c['origin'][:26]:<26} {c['statement'][:80]}{val}")
        for o in f["overrides"][:4]:
            print(f"   override: {o}")
    print("\nGAPS")
    for g in a["gaps"]:
        print(f"   [{g['type']}] {g['text'][:150]}")
    print(f"\njudge: {a['judge_notes']}\nWrote {args.run_dir / 'analysis.json'}")
    print(f"Next: python -m brief_agent.brief --run-dir {args.run_dir}   (bottom line + brief.html)")


if __name__ == "__main__":
    main()
