"""Report Writer agent — Anthropic API diretta + FastAPI, porta 8005.

Produce il report finale in italiano: executive summary + JSON strutturato.
Include un passaggio di QA interno prima di restituire l'output.
Mappa report_writer + qa_reviewer di CrewAI.
"""
import json
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any

import anthropic
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from shared.a2a_models import A2ATask, A2ATaskResult, JsonRpcRequest, JsonRpcResponse

load_dotenv(Path(__file__).parent.parent.parent / ".env")

_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

# ------------------------------------------------------------------ #
# Prompts                                                              #
# ------------------------------------------------------------------ #

_REPORT_SYSTEM = """Sei un analista di ricerca azionaria senior. Produci report in italiano professionale.

Il report ha DUE sezioni obbligatorie, separate esattamente da questi separatori:

=== SINTESI ESECUTIVA ===
(massimo 10 righe, tono neutro, nessuna direttiva buy/sell)
Focus su ciò che è specifico e differenziante per ogni candidato.

=== JSON ===
(JSON valido che rispetta esattamente lo schema fornito)

REGOLE:
- Tutto il testo in italiano
- Cita gli ID notizia per ogni affermazione (N1, N2...)
- Nessun numero inventato o data nel passato
- scoring.totale = somma esatta delle 5 dimensioni (max 50)
- data_analisi = {today}"""

_REPORT_SCHEMA = """{
  "data_analisi": "YYYY-MM-DD",
  "universo": "US e EU equities",
  "temi": [
    {
      "tema_id": "T1",
      "titolo": "string",
      "perche_ora": "string",
      "evidenze": ["N1"],
      "indicatori_da_monitorare": ["item"]
    }
  ],
  "candidati": [
    {
      "rank": 1,
      "ticker": "string",
      "azienda": "string",
      "mercato": "US|EU",
      "tema": "T1",
      "tesi": "string",
      "catalizzatore": "string",
      "orizzonte_settimane": "string",
      "scenari": {"base": "", "bull": "", "bear": ""},
      "rischi": {"macro": "", "settore": "", "azienda": "", "regolatorio": "", "valutazione": ""},
      "trigger_falsificazione": "string",
      "prossime_verifiche": ["item"],
      "evidenze_citate": ["N1"],
      "rating_qualita": "alta|media|bassa",
      "scoring": {
        "forza_catalizzatore": 0,
        "fit_orizzonte": 0,
        "asimmetria_narrativa": 0,
        "qualita_evidenze": 0,
        "rischio_crowding": 0,
        "totale": 0
      },
      "consenso_analisti": {
        "totale_analisti": 0,
        "strong_buy": 0, "buy": 0, "hold": 0, "sell": 0, "strong_sell": 0,
        "giudizio_sintetico": "string",
        "target_medio": "string"
      }
    }
  ],
  "candidati_esclusi": [{"ticker": "string", "motivo_esclusione": "string"}],
  "nota_metodologica": "string"
}"""

_QA_SYSTEM = """Sei un revisore QA di report di ricerca azionaria. Oggi è {today}.

Controlla:
1. Conformità schema JSON
2. Ogni affermazione cita un ID notizia
3. Nessuna direttiva buy/sell esplicita
4. scoring.totale = somma esatta (ogni dimensione 1-10, max 50)
5. consenso_analisti compilato per ogni candidato
6. Tutto il testo in italiano corretto
7. Date future coerenti (tutte dopo {today})

Rispondi SOLO con:
QA: [APPROVATO|CORRETTO] — una riga di verdetto, max 3 frasi.

Se ci sono correzioni numeriche (scoring, consensus):
=== CORREZIONI ===
[{{"ticker": "X", "field": "scoring.totale", "value": 31, "motivo": "reason"}}]

Non riprodurre il report. Non correggere testi liberi."""


# ------------------------------------------------------------------ #
# Core logic                                                           #
# ------------------------------------------------------------------ #

def _call_claude(system: str, user: str, model: str, max_tokens: int) -> str:
    response = _client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return response.content[0].text


def _extract_section(text: str, marker: str) -> str:
    idx = text.find(marker)
    if idx == -1:
        return ""
    after = text[idx + len(marker):].strip()
    # Stop at next === marker
    next_marker = after.find("\n===")
    if next_marker != -1:
        after = after[:next_marker]
    return after.strip()


def _extract_json(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        inner = lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
        text = "\n".join(inner).strip()
    start = text.find("{")
    end = text.rfind("}") + 1
    if start != -1 and end > start:
        return text[start:end]
    return text


async def run_agent(task: A2ATask) -> A2ATaskResult:
    input_data: dict[str, Any] = {}
    for part in task.message.parts:
        if hasattr(part, "data"):
            input_data.update(part.data)

    today = date.today().isoformat()
    candidates = input_data.get("candidates", [])
    risk_assessment = input_data.get("risk_assessment", [])
    news = input_data.get("news", [])
    themes = input_data.get("themes", [])

    user_prompt = (
        f"Oggi è {today}.\n\n"
        f"NOTIZIE:\n{json.dumps(news, ensure_ascii=False)}\n\n"
        f"TEMI:\n{json.dumps(themes, ensure_ascii=False)}\n\n"
        f"CANDIDATI:\n{json.dumps(candidates, ensure_ascii=False)}\n\n"
        f"VALUTAZIONE RISCHI:\n{json.dumps(risk_assessment, ensure_ascii=False)}\n\n"
        f"SCHEMA JSON TARGET:\n{_REPORT_SCHEMA}\n\n"
        "Produci il report completo con le due sezioni."
    )

    try:
        # Step 1 — generate report
        report_raw = _call_claude(
            system=_REPORT_SYSTEM.format(today=today),
            user=user_prompt,
            model="claude-sonnet-4-6",
            max_tokens=16000,
        )

        sintesi = _extract_section(report_raw, "=== SINTESI ESECUTIVA ===")
        json_raw = _extract_section(report_raw, "=== JSON ===")
        json_clean = _extract_json(json_raw)

        # Step 2 — QA review
        qa_input = f"REPORT DA REVISIONARE:\n{report_raw}"
        qa_output = _call_claude(
            system=_QA_SYSTEM.format(today=today),
            user=qa_input,
            model="claude-sonnet-4-6",
            max_tokens=2048,
        )

        # Parse JSON report
        try:
            report_dict = json.loads(json_clean)
        except json.JSONDecodeError:
            report_dict = {"raw": json_clean}

        return A2ATaskResult.ok(
            task.id,
            sintesi,
            data={
                "report": report_dict,
                "executive_summary": sintesi,
                "qa_verdict": qa_output,
            },
        )
    except Exception as e:
        return A2ATaskResult.fail(task.id, str(e))


# ------------------------------------------------------------------ #
# FastAPI                                                              #
# ------------------------------------------------------------------ #

app = FastAPI(title="ReportWriter A2A Agent")

_WELL_KNOWN = Path(__file__).parent / ".well-known" / "agent.json"


@app.get("/.well-known/agent.json")
async def agent_card():
    return FileResponse(_WELL_KNOWN, media_type="application/json")


@app.post("/tasks")
async def receive_task(rpc: JsonRpcRequest) -> JSONResponse:
    if rpc.method != "tasks/send":
        resp = JsonRpcResponse.fail(-32601, f"Method not found: {rpc.method}", rpc.id)
        return JSONResponse(resp.model_dump(), status_code=404)
    try:
        task = A2ATask(**rpc.params)
    except Exception as e:
        resp = JsonRpcResponse.fail(-32602, f"Invalid params: {e}", rpc.id)
        return JSONResponse(resp.model_dump(), status_code=422)

    result = await run_agent(task)
    return JSONResponse(JsonRpcResponse.ok(result.model_dump(), rpc.id).model_dump())


@app.get("/health")
async def health():
    return {"status": "ok", "agent": "ReportWriter", "port": 8005}


# ------------------------------------------------------------------ #
# Entry point                                                          #
# ------------------------------------------------------------------ #

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8005, log_level="info")
