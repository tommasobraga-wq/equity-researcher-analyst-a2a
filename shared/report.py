"""HTML report generator for the A2A pipeline output."""
import json
import re
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from pydantic import ValidationError

from shared.models import Candidato, Correction, Report
from shared.validators import Violation, validate

_SCORING_DIMS: list[tuple[str, str]] = [
    ("forza_catalizzatore",  "Forza catalizzatore"),
    ("fit_orizzonte",        "Fit orizzonte"),
    ("asimmetria_narrativa", "Asimmetria narrativa"),
    ("qualita_evidenze",     "Qualità evidenze"),
    ("rischio_crowding",     "Rischio crowding"),
]

_JINJA_ENV = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "templates"),
    autoescape=select_autoescape(["html"]),
)

_OUTPUT_DIR = Path(__file__).parent.parent / "output"


def _parse_corrections(qa_verdict: str) -> list[Correction]:
    """Extract JSON corrections array from QA verdict string."""
    if not qa_verdict or "APPROVATO" in qa_verdict:
        return []
    array_match = re.search(r"\[.*?\]", qa_verdict, re.DOTALL)
    if not array_match:
        return []
    try:
        raw = json.loads(array_match.group())
        return [Correction(**item) for item in raw if isinstance(item, dict)]
    except (json.JSONDecodeError, TypeError, ValueError):
        return []


def _parse_report(report_dict: dict) -> tuple[Report | None, bool]:
    """Validate report dict into Pydantic model."""
    if not report_dict:
        return None, True
    try:
        return Report.model_validate(report_dict), False
    except ValidationError:
        return None, True


def _auto_fix_scoring(report: Report) -> Report:
    for c in report.candidati:
        correct = sum(getattr(c.scoring, key) for key, _ in _SCORING_DIMS)
        if c.scoring.totale != correct:
            c.scoring.totale = correct
    return report


_DISPLAY_CORRECTION_PREFIXES = ("scoring.", "consenso_analisti.")


def _apply_corrections(report: Report, corrections: list[Correction]) -> tuple[Report, list[str]]:
    applied = []
    ticker_map = {c.ticker: c for c in report.candidati}
    for fix in corrections:
        candidate = ticker_map.get(fix.ticker)
        if candidate is None:
            continue
        parts = fix.field.split(".")
        show = any(fix.field.startswith(p) for p in _DISPLAY_CORRECTION_PREFIXES)
        try:
            if len(parts) == 1:
                old = getattr(candidate, parts[0])
                coerced = type(old)(fix.value)
                setattr(candidate, parts[0], coerced)
                if show:
                    applied.append(f"{fix.ticker} · {fix.field}: {old} → {coerced} ({fix.motivo})")
            elif len(parts) == 2:
                sub = getattr(candidate, parts[0])
                old = getattr(sub, parts[1])
                coerced = type(old)(fix.value)
                setattr(sub, parts[1], coerced)
                if show:
                    applied.append(f"{fix.ticker} · {fix.field}: {old} → {coerced} ({fix.motivo})")
        except (AttributeError, ValueError, TypeError):
            continue
    return report, applied


def generate_html(
    executive_summary: str,
    report_dict: dict,
    qa_verdict: str,
    tickers: list[str],
    execution_seconds: int | None = None,
    run_id: str | None = None,
) -> tuple[Path, list[Violation]]:
    report, json_failed = _parse_report(report_dict)
    now = datetime.now()
    timestamp = run_id or now.strftime("%Y%m%d_%H%M%S")
    label = now.strftime("%d %B %Y, %H:%M")

    corrections = _parse_corrections(qa_verdict)
    applied_corrections: list[str] = []
    tema_map: dict[str, list[Candidato]] = {}

    if report:
        report = _auto_fix_scoring(report)
        if corrections:
            report, applied_corrections = _apply_corrections(report, corrections)
        for c in report.candidati:
            tema_map.setdefault(c.tema, []).append(c)

    exec_time_str = (
        f"{execution_seconds // 60}m {execution_seconds % 60}s"
        if execution_seconds is not None else ""
    )

    context = {
        "Tickers": ", ".join(tickers),
        "Universo": "US & EU Equity",
    }

    violations = validate(report)

    template = _JINJA_ENV.get_template("report.html.j2")
    html = template.render(
        label=label,
        context=context,
        sintesi=executive_summary,
        json_failed=json_failed,
        applied_corrections=applied_corrections,
        report=report,
        tema_map=tema_map,
        exec_time_str=exec_time_str,
        scoring_dims=_SCORING_DIMS,
        violations=violations,
    )

    _OUTPUT_DIR.mkdir(exist_ok=True)
    path = _OUTPUT_DIR / f"report_{timestamp}.html"
    path.write_text(html, encoding="utf-8")

    # Save raw pipeline state for debugging
    raw_path = _OUTPUT_DIR / f"raw_{timestamp}.json"
    raw_data = {
        "executive_summary": executive_summary,
        "report": report_dict,
        "qa_verdict": qa_verdict,
    }
    raw_path.write_text(
        json.dumps(raw_data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return path, violations
