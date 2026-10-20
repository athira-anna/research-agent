"""Run stage 3 on its own:  python -m brief_agent.extract --run-dir runs/<id> --topic "..." """

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import sys

from ..llm import MISSING_KEY_HELP, GeminiLLM, has_api_key
from .pipeline import extract_claims


def main() -> None:
    p = argparse.ArgumentParser(prog="python -m brief_agent.extract", description=__doc__)
    p.add_argument("--run-dir", type=Path, required=True, help="a run folder with sources.json and plan.json")
    p.add_argument("--topic", required=True)
    p.add_argument("--verbose", "-v", action="store_true", help="also print quotes and fixes")
    args = p.parse_args()
    if not has_api_key():
        sys.exit(f"error: {MISSING_KEY_HELP}")

    claims, sources = asyncio.run(extract_claims(args.run_dir, args.topic, GeminiLLM()))
    by_source: dict[str, list] = {}
    for c in claims:
        by_source.setdefault(c.source_id, []).append(c)

    for s in sources:
        e = s["extraction"]
        print(f"\n{s['source_id']}  extraction {e['status']:<8} claims {e.get('claims', 0):>2}  dropped {e.get('dropped', 0):>2} "
              f"{e.get('dropped_by_reason', '') or ''}  {e.get('model', '')}")
        if e.get("reason"):
            print(f"      {e['reason'][:160]}")
        for c in by_source.get(s["source_id"], []):
            val = f" [{c.value.value_kind} {c.value.low:g} {c.value.unit} · {c.value.metric_id}]" if c.value else ""
            tags = f"{c.modality}/{c.certainty}" + (" causal" if c.causal else "") + f" · support {c.support.get('status')}"
            print(f"   {c.claim_id} {c.question_id:<5} {c.statement[:110]}{val}")
            print(f"            {tags}" + (f" · from {c.evidence.attributed_to}" if c.evidence.attributed_to else ""))
            if args.verbose:
                print(f"            quote [{c.evidence.block_id}]: \"{c.evidence.quote[:140]}\"")
                for f in c.fixes:
                    print(f"            fix: {f}")
    print(f"\nWrote {args.run_dir / 'claims.json'}, dropped_claims.json, and updated sources.json")


if __name__ == "__main__":
    main()
