"""Strict timecode parsing shared by AI, subtitles and the editor."""
import re

_TIME = re.compile(r"^(\d{1,3}):(\d{2}):(\d{2})[.,](\d{3})$")


def parse_time(value: str) -> float:
    match = _TIME.fullmatch(value.strip())
    if not match:
        raise ValueError(f"Invalid timecode: {value!r}; expected HH:MM:SS.mmm")
    hours, minutes, seconds, millis = map(int, match.groups())
    if minutes >= 60 or seconds >= 60:
        raise ValueError(f"Invalid timecode: {value!r}")
    return hours * 3600 + minutes * 60 + seconds + millis / 1000


def format_time(seconds: float, separator: str = ".") -> str:
    if seconds < 0:
        raise ValueError("Negative timestamps are not allowed")
    millis = round(seconds * 1000)
    hours, remainder = divmod(millis, 3600000)
    minutes, remainder = divmod(remainder, 60000)
    secs, ms = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02}{separator}{ms:03}"
