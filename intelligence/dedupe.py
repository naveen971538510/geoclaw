from typing import Dict, List

from .quality import canonicalize_url, headline_similarity, headline_signature, trust_score, ts


def _candidate_rank(item: Dict):
    return (
        trust_score(item.get("source_name", ""), item.get("url", "")),
        len(str(item.get("summary", "") or "")),
        ts(item.get("published_at", "")),
        len(str(item.get("headline", "") or "")),
    )


def _better_candidate(left: Dict, right: Dict) -> bool:
    return _candidate_rank(left) > _candidate_rank(right)


def _is_near_duplicate(left: Dict, right: Dict) -> bool:
    left_sig = left.get("_signature") or tuple()
    right_sig = right.get("_signature") or tuple()
    if not left_sig or not right_sig:
        return False
    if left_sig == right_sig:
        return True

    similarity = headline_similarity(left.get("headline", ""), right.get("headline", ""))
    if similarity < 0.72:
        return False

    left_url = left.get("_canonical_url", "")
    right_url = right.get("_canonical_url", "")
    if left_url and right_url and left_url == right_url:
        return True

    left_summary = str(left.get("summary", "") or "")
    right_summary = str(right.get("summary", "") or "")
    if left_summary and right_summary and headline_similarity(left_summary, right_summary) >= 0.55:
        return True
    return False


def dedupe_articles(items: List[Dict]) -> List[Dict]:
    by_url = {}
    out: List[Dict] = []

    for item in items:
        candidate = dict(item)
        candidate["_canonical_url"] = canonicalize_url(candidate.get("url", ""))
        candidate["_signature"] = headline_signature(candidate.get("headline", ""))

        canonical_url = candidate["_canonical_url"]
        if canonical_url:
            existing = by_url.get(canonical_url)
            if existing is None:
                by_url[canonical_url] = candidate
                out.append(candidate)
            elif _better_candidate(candidate, existing):
                out[out.index(existing)] = candidate
                by_url[canonical_url] = candidate
            continue

        replaced = False
        for idx, existing in enumerate(out):
            if _is_near_duplicate(candidate, existing):
                if _better_candidate(candidate, existing):
                    out[idx] = candidate
                replaced = True
                break
        if not replaced:
            out.append(candidate)

    for item in out:
        item.pop("_canonical_url", None)
        item.pop("_signature", None)
    return out
