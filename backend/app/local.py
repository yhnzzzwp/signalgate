"""Run the built dashboard and API with inference restricted to local Ollama."""
from __future__ import annotations

import argparse
import os

from app.config import REPO_ROOT, get_settings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="periksa build dan model tanpa memulai server")
    args = parser.parse_args()
    if not (REPO_ROOT / "frontend" / "dist" / "index.html").is_file():
        raise SystemExit("Build dashboard dahulu: cd frontend && npm ci && npm run build")
    os.environ["LOCAL_ONLY"] = "true"
    os.environ["FRONTIER_ENABLED"] = "false"
    os.environ["LLM_BACKEND"] = "ollama"
    get_settings.cache_clear()
    from app.runtime import resolve
    from app.llm.factory import make_agent
    from app.workflow.models import resolve_models, resolve_reviewers
    from app.research.agents import AgentError

    settings = resolve(get_settings(), None).settings
    models = list(dict.fromkeys(filter(None, [
        settings.ollama_model, settings.ollama_validator_model,
        *settings.ollama_reviewer_models, resolve_models(settings)[0], *resolve_reviewers(settings),
    ])))
    for name in models:
        agent = make_agent(settings, name)
        try:
            agent.check_ready()
        except AgentError as error:
            raise SystemExit(str(error)) from error
        finally:
            agent.close()
    print(f"Model lokal: {', '.join(models)}", flush=True)
    if args.check:
        return
    print("Dashboard: http://127.0.0.1:8000 | Hentikan dengan Ctrl+C", flush=True)
    import uvicorn

    # One worker: job locks and SSE history belong to this process.
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, workers=1)


if __name__ == "__main__":
    main()
