"""Pydantic models for the report JSON schema produced by ReportWriter."""
from pydantic import BaseModel, ConfigDict


class _Mutable(BaseModel):
    model_config = ConfigDict(frozen=False)


class Scoring(_Mutable):
    forza_catalizzatore: int = 0
    fit_orizzonte: int = 0
    asimmetria_narrativa: int = 0
    qualita_evidenze: int = 0
    rischio_crowding: int = 0
    totale: int = 0


class Scenari(_Mutable):
    base: str = ""
    bull: str = ""
    bear: str = ""


class Rischi(_Mutable):
    macro: str = ""
    settore: str = ""
    azienda: str = ""
    regolatorio: str = ""
    valutazione: str = ""


class ConsensoCandidato(_Mutable):
    totale_analisti: int = 0
    strong_buy: int = 0
    buy: int = 0
    hold: int = 0
    sell: int = 0
    strong_sell: int = 0
    giudizio_sintetico: str = ""
    target_medio: str = ""


class Candidato(_Mutable):
    rank: int = 0
    ticker: str = ""
    azienda: str = ""
    mercato: str = ""
    tema: str = ""
    tesi: str = ""
    catalizzatore: str = ""
    orizzonte_settimane: str = ""
    scenari: Scenari = Scenari()
    rischi: Rischi = Rischi()
    trigger_falsificazione: str = ""
    prossime_verifiche: list[str] = []
    evidenze_citate: list[str] = []
    rating_qualita: str = ""
    scoring: Scoring = Scoring()
    consenso_analisti: ConsensoCandidato = ConsensoCandidato()


class CandidatoEscluso(_Mutable):
    ticker: str = ""
    motivo_esclusione: str = ""


class Tema(_Mutable):
    tema_id: str = ""
    titolo: str = ""
    perche_ora: str = ""
    evidenze: list[str] = []
    indicatori_da_monitorare: list[str] = []


class VoceAllocazione(_Mutable):
    ticker: str = ""
    peso_pct: float = 0.0
    razionale: str = ""


class Report(_Mutable):
    data_analisi: str = ""
    universo: str = ""
    temi: list[Tema] = []
    candidati: list[Candidato] = []
    candidati_esclusi: list[CandidatoEscluso] = []
    allocazione: list[VoceAllocazione] = []
    nota_allocazione: str = ""
    nota_metodologica: str = ""


class Correction(_Mutable):
    ticker: str
    field: str
    value: int | float | str
    motivo: str = ""
