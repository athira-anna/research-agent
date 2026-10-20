"""Step 5.6: what nobody covers. Plain code over the plan, the claims and the source statuses."""

from __future__ import annotations

import re

from ..extract.grounding import normalize
from ..plan.models import Plan


def _display(label: str, sources: list[dict]) -> str:
    """'source s3' → the source's publisher (site name or domain), for reader-facing text."""
    if not label.startswith("source "):
        return label
    sid = label.split()[-1]
    s = next((x for x in sources if x["source_id"] == sid), {})
    site = (s.get("meta") or {}).get("site_name")
    if site and site.lower() not in {"news", "home", "blog", "article", "newsroom"}:
        return site
    return re.sub(r"^www\.", "", re.sub(r"^https?://", "", s.get("url", "")).split("/")[0]) or label


def find_gaps(plan: Plan, claims: list[dict], alignment: dict, sources: list[dict]) -> list[dict]:
    gaps: list[dict] = []
    by_q: dict[str, list[dict]] = {}
    for c in claims:
        by_q.setdefault(c["question_id"], []).append(c)
    acl = alignment["claims"]

    for q in plan.questions:
        qc = by_q.get(q.id, [])
        if not qc:
            gaps.append({"type": "unanswered_question", "question_id": q.id,
                         "text": f"No source addresses: {q.text}"})
            continue
        origins = {acl[c["claim_id"]]["label"] for c in qc}
        if len(origins) == 1:
            gaps.append({"type": "thin_question", "question_id": q.id,
                         "text": f"Only {_display(next(iter(origins)), sources)} addresses: {q.text}"})
        for f in q.facets:
            if f.dimension not in ("population", "geography"):
                continue
            covered = [acl[c["claim_id"]]["canonical_scope"][f.dimension] for c in qc]
            covered = [normalize(x) for x in covered if x]
            if not covered:
                gaps.append({"type": "facet_gap", "question_id": q.id,
                             "text": f"No claim on '{q.text}' says which {f.name} it covers."})
                continue
            missing = [e for e in f.examples if not any(normalize(e) in x or x in normalize(e) for x in covered)]
            if missing:
                gaps.append({"type": "facet_gap", "question_id": q.id,
                             "text": f"No claim on '{q.text}' covers {f.name}: {', '.join(missing)}."})

    measured = {c["value"]["metric_id"] for c in claims if c.get("value")}
    for m in plan.metrics:
        if m.id not in measured and m.id != "reported_magnitude":
            gaps.append({"type": "no_figures", "metric_id": m.id,
                         "text": f"No source gives a figure for {m.label.lower()} ({m.definition})"})

    for s in sources:
        fetch = s.get("fetch", {}).get("status")
        ext = (s.get("extraction") or {}).get("status")
        if fetch != "ok" or ext == "failed":
            why = f"fetch: {fetch}" + (f" ({s['fetch'].get('status_detail')})" if s.get("fetch", {}).get("status_detail") else "") \
                if fetch != "ok" else f"extraction failed: {(s.get('extraction') or {}).get('reason', '')[:80]}"
            gaps.append({"type": "not_read", "source_id": s["source_id"],
                         "text": f"{s['url']} could not be used ({why}); it may cover parts of this topic."})
    return gaps
