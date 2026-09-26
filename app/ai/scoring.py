"""Deterministic ranking and de-duplication; scores are not performance forecasts."""
from difflib import SequenceMatcher
from app.ai.schemas import Candidate
from app.core.timecode import parse_time


def score(candidate: Candidate, preferred: float = 35) -> float:
    s = candidate.scores
    base = (s.hook * .22 + s.curiosity * .12 + s.value * .17 + s.pacing * .10
            + s.payoff * .17 + s.completeness * .22) * 10
    return round(max(0, base - min(10, abs(candidate.duration-preferred) * .2)), 1)


def similar(a: Candidate, b: Candidate) -> bool:
    a0, a1 = parse_time(a.start), parse_time(a.end)
    b0, b1 = parse_time(b.start), parse_time(b.end)
    overlap = max(0, min(a1, b1) - max(a0, b0)) / min(a1-a0, b1-b0)
    hook = SequenceMatcher(None, a.hook.casefold(), b.hook.casefold()).ratio()
    topic = SequenceMatcher(None, a.topic.casefold(), b.topic.casefold()).ratio()
    return overlap >= .55 or hook >= .82 or (topic >= .88 and overlap > .15)


def rank(candidates: list[Candidate], min_duration: float, max_duration: float,
         preferred: float, count: int, source_duration: float) -> list[tuple[Candidate, float]]:
    valid = [c for c in candidates if min_duration <= c.duration <= max_duration
             and parse_time(c.end) <= source_duration + .25]
    ordered = sorted(valid, key=lambda c: score(c, preferred), reverse=True)
    chosen = []
    for candidate in ordered:
        if not any(similar(candidate, previous) for previous, _ in chosen):
            chosen.append((candidate, score(candidate, preferred)))
        if len(chosen) >= count:
            break
    return chosen
