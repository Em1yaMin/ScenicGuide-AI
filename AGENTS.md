# Project agent memory

This file is the project's committed home for project-intrinsic agent knowledge: build, test, release, architecture, and sharp-edge notes that should travel with the code.

- Add durable project-specific notes here as they are discovered through real work.

## Knowledge base admin

- Backend: `digitalHumanApi/app/routers/knowledge.py` exposes `/api/admin/knowledge*` CRUD + import endpoints protected by `X-Admin-Token`.
- Token source: `ADMIN_API_TOKEN` env var (`digitalHumanApi/app/core/config.py`).
- Frontend: `digitalHumanVue/src/views/admin/KnowledgeView.vue`; token is stored in `localStorage` under `admin_api_token`.
- Import supports `.json` arrays and `.md` files split by `# ` headings.

## CI (GitHub Actions)

- Workflow: `.github/workflows/ci.yml` — backend `pytest` (Python 3.12) + frontend `npm run build` (Node 22), parallel on push to master / PR.

## Testing (pytest)

- Tests use in-memory SQLite (`tests/conftest.py` fixtures), no DATABASE_URL / .env needed.
- Gotcha: `app/core/config.py` instantiates `Settings()` at import with a validator that rejects empty Dify env vars — running pytest without `.env` or placeholder `DIFY_API_URL`/`DIFY_API_KEY`/`DIFY_WORKFLOW_API_KEY`/`DIFY_RECOMMEND_WORKFLOW_API_KEY` raises ImportError. CI injects dummy values (tests never call real APIs).

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
