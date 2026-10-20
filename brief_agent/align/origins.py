"""Who is actually saying it? Several sources repeating one study's figure are one origin, not consensus."""

from __future__ import annotations

import re

from rapidfuzz import fuzz

from ..extract.grounding import normalize

_SUFFIXES = re.compile(r"\b(inc|ltd|llc|plc|corp|corporation|co|group)\b\.?")


def _clean(name: str) -> str:
    n = normalize(name)
    n = re.sub(r"^(the|a)\s+", "", n)
    n = re.sub(r"\b(\d{4}|survey|study|report|analysis|data|research|estimates?)\b", " ", n)
    n = _SUFFIXES.sub(" ", n)
    return " ".join(re.sub(r"[^\w\s&]", " ", n).split())


_STOP = {"of", "the", "and", "for", "on", "in", "&"}


def _initials(clean: str) -> str:
    return "".join(w[0] for w in clean.split() if w not in _STOP)


def _same_org(a: str, b: str) -> bool:
    if a == b or fuzz.ratio(a, b) >= 90:
        return True
    if a in b.split(" ") or b in a.split(" ") or a.startswith(b + " ") or b.startswith(a + " "):
        return True
    # "cbo" ↔ "congressional budget office", "epi" ↔ "economic policy institute"
    for short, long in ((a, b), (b, a)):        # also "imf staff" ↔ "international monetary fund"
        words = short.split()
        if len(words) <= 2 and len(long.split()) >= 2 and len(_initials(long)) >= 2 and _initials(long) in words:
            return True
    return False


def assign_origins(claims: list[dict], sources: list[dict]) -> dict[str, dict]:
    """claim_id → {origin, label}. Origin = the cited study/organisation (near-identical names merged),
    otherwise the source itself; stage 2 duplicate sources share their original's origin."""
    dup_of = {s["source_id"]: (s.get("duplicate") or {}).get("of") for s in sources}
    keys: list[tuple[str, str]] = []            # (clean name, display label), first spelling wins
    out = {}
    for c in claims:
        name = (c["evidence"].get("attributed_to") or "").strip()
        clean = _clean(name) if name else ""
        if clean:
            match = next((k for k in keys if _same_org(k[0], clean)), None)
            if match is None:
                match = (clean, name)
                keys.append(match)
            out[c["claim_id"]] = {"origin": f"org:{match[0]}", "label": match[1]}
        else:
            sid = dup_of.get(c["source_id"]) or c["source_id"]
            out[c["claim_id"]] = {"origin": f"source:{sid}", "label": f"source {sid}"}
    return out
