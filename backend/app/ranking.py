"""Rank fusion and bounded exact reference extraction; neither grants permissions."""
import re


def reciprocal_rank_fusion(rankings, k=60):
    scores = {}
    for ranking in rankings:
        for rank, key in enumerate(dict.fromkeys(ranking), 1):
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
    return scores


def exact_references(query):
    return sorted({m.group().casefold() for m in re.finditer(
        r"\b(?:[a-z]{2,12}[-_/]\d[a-z0-9_/-]{1,30}|\d{4}-\d{2}-\d{2})\b", query, re.I)})
