"""
Backend for the Playwright end-to-end tests (frontend/tests/e2e).

Starts the real FastAPI app on 127.0.0.1:8000 (the address the frontend
calls) against a brand-new SQLite database, seeds the test accounts in
tests/support/e2e_users.json, and -- unless E2E_REAL_LLM=1 -- replaces
the AI provider with the deterministic fake used by the pytest suite, so
browser tests are fast, free and repeatable.

Any assessment whose text contains SIMULATE_AI_OUTAGE gets a provider
timeout instead of an answer, so the degraded (rules-only) path can be
exercised from the browser.

    python tests/e2e_server.py          (from backend/; Playwright does this)
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
os.chdir(BACKEND_DIR)

USERS = json.loads((BACKEND_DIR / "tests" / "support" / "e2e_users.json").read_text(encoding="utf-8"))
PORT = int(os.getenv("E2E_BACKEND_PORT") or 8000)

_db_dir = Path(os.getenv("E2E_DB_DIR") or tempfile.mkdtemp(prefix="raw-e2e-"))
_db_dir.mkdir(parents=True, exist_ok=True)
_db_file = _db_dir / "e2e.db"
_db_file.unlink(missing_ok=True)

os.environ.update(
    {
        "DATABASE_URL": f"sqlite:///{_db_file}",
        "ADMIN_BOOTSTRAP_EMAIL": USERS["admin"]["email"],
        "ADMIN_BOOTSTRAP_PASSWORD": USERS["admin"]["password"],
        "WORKFLOW_ESCALATION_INTERVAL_SECONDS": "0",
        "BACKUP_INTERVAL_HOURS": "0",
        "BACKUP_DIR": str(_db_dir / "backups"),
        "OPENROUTER_MAX_ATTEMPTS": "1",
        "OPENROUTER_BACKOFF_SECONDS": "0",
        "CORS_ALLOWED_ORIGINS": ",".join(
            f"http://{host}:{port}" for host in ("localhost", "127.0.0.1") for port in (5173, 5176, 4173)
        ),
        "LANGFUSE_ENABLED": os.getenv("E2E_LANGFUSE_ENABLED", "false"),
    }
)


if os.getenv("E2E_REAL_LLM") != "1":
    # Every E2E AI call is the fake; never the real provider/key.
    os.environ.update({"LLM_PROVIDER": "openrouter", "OPENAI_API_KEY": ""})
# E2E_REAL_LLM=1: LLM_PROVIDER and the API key come from backend/.env.


def _install_fake_llm() -> None:
    import requests

    import app.ai.likelihood_impact_analyzer as likelihood
    import app.ai.metering as metering
    import json

    import app.ai.risk_factor_analyzer as analyzer
    import app.document_analysis.ai_extractor as extractor
    from tests.support.fake_llm import FakeLLM, chat, default_reply, extraction_for, prompt_of

    def transport(url, payload):
        if "SIMULATE_AI_OUTAGE" in prompt_of(payload):
            raise requests.Timeout("simulated provider timeout (E2E)")
        # P4: document extraction quotes the document's labelled lines, so
        # field provenance is exercised end to end.
        extraction = extraction_for(prompt_of(payload))
        if extraction is not None:
            return chat(json.dumps(extraction))
        return default_reply(url, payload)

    fake = FakeLLM()
    fake.transport = transport
    metering.requests.post = fake
    analyzer.OPENROUTER_API_KEY = likelihood.OPENROUTER_API_KEY = "e2e-fake-key"
    # Never the real key from backend/.env: every E2E AI call is the fake.
    extractor.OPENROUTER_API_KEY = "e2e-fake-key"


def _seed_users() -> None:
    from app.auth.security import hash_password
    from app.database import SessionLocal
    from app.models.user import User

    db = SessionLocal()
    try:
        manager_id = None
        # Manager first: the owner, analysts and the dual-role admin report
        # to them. P3: governance designations come from the JSON.
        for key in ("manager", "owner", "analyst", "committee", "reviewer", "head", "chair", "dualadmin", "fcrmrep", "businessrep"):
            spec = USERS[key]
            if db.query(User).filter(User.email == spec["email"]).first():
                continue
            user = User(
                email=spec["email"],
                hashed_password=hash_password(spec["password"]),
                full_name=spec["full_name"],
                role=spec["role"],
                manager_id=manager_id if key in {"owner", "analyst", "reviewer", "dualadmin"} else None,
            )
            user.set_designations(spec.get("designations"))
            db.add(user)
            db.flush()
            if key == "manager":
                manager_id = user.id
        db.commit()
    finally:
        db.close()


def main() -> None:
    import uvicorn

    if os.getenv("E2E_REAL_LLM") != "1":
        _install_fake_llm()

    from app.main import app, prepare_database

    # Importing the app never migrates; prepare this run's throwaway
    # SQLite database explicitly (migrations + bootstrap admin).
    prepare_database()

    _seed_users()
    print(f"E2E backend on http://127.0.0.1:{PORT} (database {_db_file})", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
