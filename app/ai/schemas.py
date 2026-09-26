"""Pydantic contracts for external model responses."""
from pydantic import BaseModel, Field, model_validator
from app.core.timecode import parse_time

class Scores(BaseModel):
    hook: float = Field(ge=0, le=10)
    curiosity: float = Field(ge=0, le=10)
    value: float = Field(ge=0, le=10)
    pacing: float = Field(ge=0, le=10)
    payoff: float = Field(ge=0, le=10)
    completeness: float = Field(ge=0, le=10)

class Candidate(BaseModel):
    id: int
    start: str
    end: str
    duration: float = Field(gt=0)
    topic: str = Field(min_length=1)
    hook: str = Field(min_length=1)
    summary: str
    reason: str
    keywords: list[str] = Field(default_factory=list)
    scores: Scores

    @model_validator(mode="after")
    def valid_range(self):
        actual = parse_time(self.end) - parse_time(self.start)
        if actual <= 0 or abs(actual - self.duration) > 1:
            raise ValueError("Candidate timestamps do not match duration")
        return self

class Analysis(BaseModel):
    candidates: list[Candidate]

class Metadata(BaseModel):
    titles: list[str] = Field(min_length=1)
    description: str
    tags: list[str] = Field(default_factory=list)
    hashtags: list[str] = Field(default_factory=list)
    category_id: str = "22"
    keywords: list[str] = Field(default_factory=list)
