"""Pydantic models for the intermediate (per-stage) outputs of the pipeline.

Distinct from shared/models.py, which is the schema of the ReportWriter's
final output only. These models are intentionally lenient (most fields
optional / loosely typed) since they validate raw LLM JSON, not a
hand-authored schema — the goal is to catch missing/malformed structure,
not to enforce a rigid contract.
"""
from pydantic import BaseModel, ConfigDict


class _Lenient(BaseModel):
    model_config = ConfigDict(extra="allow")


class FundamentalRecord(_Lenient):
    ticker: str
    price: float | str | None = None
    pe_ttm: float | str | None = None
    week52_low: float | str | None = None
    week52_high: float | str | None = None


class NewsItem(_Lenient):
    id: str
    source: str = ""
    headline: str = ""
    summary: str = ""


class ThemeItem(_Lenient):
    id: str
    title: str = ""
    why_now: str = ""
    news_ids: list[str] = []


class NewsThemesOutput(BaseModel):
    news: list[NewsItem]
    themes: list[ThemeItem]


class CandidateFundamentals(_Lenient):
    price: str | float | None = None
    pe_ttm: str | float | None = None
    eps: str | float | None = None


class AnalystConsensus(_Lenient):
    total_analysts: int = 0
    giudizio_sintetico: str = ""


class CandidateItem(_Lenient):
    ticker: str
    company: str = ""
    market: str = ""
    theme_id: str = ""
    thesis: str = ""
    catalyst: str = ""
    news_ids: list[str] = []
    fundamentals: CandidateFundamentals = CandidateFundamentals()
    analyst_consensus: AnalystConsensus = AnalystConsensus()


class RiskScoring(_Lenient):
    forza_catalizzatore: int = 0
    fit_orizzonte: int = 0
    asimmetria_narrativa: int = 0
    qualita_evidenze: int = 0
    rischio_crowding: int = 0
    totale: int = 0


class RiskItem(_Lenient):
    ticker: str
    scenarios: dict = {}
    risks: dict = {}
    falsification: str = ""
    next_checks: list[str] = []
    quality: str = ""
    scoring: RiskScoring = RiskScoring()
