"""Parse, validate and shift SubRip captions without AI."""
import re
from dataclasses import dataclass
from pathlib import Path
from app.core.timecode import parse_time, format_time

@dataclass(frozen=True)
class Cue:
    start: float
    end: float
    text: str


def parse_srt(text: str) -> list[Cue]:
    cues = []
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n").strip()):
        lines = block.strip().splitlines()
        if not lines:
            continue
        timing = next((line for line in lines if " --> " in line), None)
        if timing is None:
            raise ValueError("SRT block has no time range")
        start_s, end_s = timing.split(" --> ", 1)
        start, end = parse_time(start_s), parse_time(end_s)
        if end <= start or (cues and start < cues[-1].end):
            raise ValueError("SRT contains reversed or overlapping timestamps")
        content = "\n".join(lines[lines.index(timing) + 1:]).strip()
        if not content:
            raise ValueError("SRT block has no caption text")
        cues.append(Cue(start, end, content))
    return cues


def load_srt(path: str | Path) -> list[Cue]:
    return parse_srt(Path(path).read_text(encoding="utf-8-sig"))


def shift_srt(cues: list[Cue], start: float, end: float) -> str:
    if end <= start:
        raise ValueError("Invalid clip range")
    result = []
    for cue in cues:
        left, right = max(cue.start, start), min(cue.end, end)
        if right <= left:
            continue
        result.append(f"{len(result)+1}\n{format_time(left-start, ',')} --> {format_time(right-start, ',')}\n{cue.text}\n")
    return "\n".join(result)


def transcript(cues: list[Cue]) -> str:
    return "\n".join(f"[{format_time(c.start)} - {format_time(c.end)}] {c.text}" for c in cues)
