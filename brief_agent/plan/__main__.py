"""Run stage 0 on its own:  python -m brief_agent.plan --topic "..." --run-dir runs/<id>"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from ..llm import GeminiLLM
from .planner import PlanFileError, get_plan
from .topic import TopicError


def main() -> None:
    p = argparse.ArgumentParser(prog="python -m brief_agent.plan", description=__doc__)
    p.add_argument("--topic", required=True)
    p.add_argument("--run-dir", type=Path, required=True)
    p.add_argument("--replan", action="store_true", help="ignore a saved plan.json and make a new one")
    p.add_argument("--plan-file", type=Path, help="use this hand-written/edited plan (same topic required)")
    p.add_argument("--no-llm", action="store_true", help="use the generic fallback plan")
    args = p.parse_args()

    try:
        plan, how = asyncio.run(get_plan(args.topic, args.run_dir, None if args.no_llm else GeminiLLM(),
                                         replan=args.replan, plan_file=args.plan_file))
    except (TopicError, PlanFileError) as e:
        sys.exit(f"error: {e}")

    print(f"plan: {how} · source: {plan.source} · model: {plan.model or '-'}")
    print(f"\ninterpretation: {plan.interpretation}")
    for a in plan.assumptions:
        print(f"assumption:     {a}")
    print(f"\nsubject terms:  {', '.join(plan.subject_terms.core)}")
    print(f"synonyms:       {', '.join(plan.subject_terms.synonyms) or '-'}")
    print("\nmetrics:")
    for m in plan.metrics:
        conf = f"  (≠ {', '.join(m.confusable_with)})" if m.confusable_with else ""
        print(f"  {m.id:<28} {m.measure_type}/{m.unit:<18} {m.useful_for}{conf}")
    print("\nquestions:")
    for q in plan.questions:
        print(f"  {q.id} [{q.angle}, {q.kind}] {q.text}")
        if q.metric_ids:
            print(f"      metrics: {', '.join(q.metric_ids)}")
        if q.facets:
            print(f"      facets:  {'; '.join(f'{f.dimension}:{f.name}' for f in q.facets)}")
        if q.question_terms:
            print(f"      terms:   {', '.join(q.question_terms)}")
    for f in plan.fixes:
        print(f"\nfix: {f}")
    print(f"\nWrote {args.run_dir / 'plan.json'}")


if __name__ == "__main__":
    main()
