"""Ingests policies/docs/*.md and *.pdf into the pgvector policy_chunks table.

Usage:
    uv run python scripts/ingest_policies.py

Re-run after adding/editing policy documents in policies/docs/ — ingestion
replaces any existing chunks for a given source file, so it's always safe to
re-run on the current state of that directory. PDFs are routed through
structure-aware parsers (shared/policy_pdf.py) keyed by filename — adding a
new regulatory PDF requires registering a parser there first.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

from shared.policy_store import ingest_documents

load_dotenv(Path(__file__).parent.parent / ".env")

_DOCS_DIR = Path(__file__).parent.parent / "policies" / "docs"


async def main() -> None:
    paths = sorted(_DOCS_DIR.glob("*.md")) + sorted(_DOCS_DIR.glob("*.pdf"))
    if not paths:
        print(f"Nessun documento trovato in {_DOCS_DIR}")
        return
    print(f"Ingestion di {len(paths)} documento/i da {_DOCS_DIR}...")
    total = await ingest_documents(paths)
    print(f"Completato: {total} chunk indicizzati.")


if __name__ == "__main__":
    asyncio.run(main())
