# CLAUDE.md

ewald-simagent is an EWALD plugin that opens a user's finished sim-agent calculations by AiiDA PK
(`load("simagent:1234")`) for visualization and analysis. Read-only by design: it never submits,
kills or changes calculations; that stays in sim-agent with its guards.

- The owner writes Korean with English technical terms; answer the same way. Code, commits and docs in English.
- The HTTP contract is sim-agent's results API v1 (`sim-agent.node/1`: Bearer tokens from
  `POST /api/v1/tokens`, `/api/v1/nodes/{node}`, `.../files/{name}`, `.../structure.extxyz`),
  owned by frssp/sim_agent `docs/api/results-api-v1.md`. Change it there first; keep
  `tests/conftest.py`'s fake backend matching it, and bump `client.SCHEMA` only together with sim-agent.
  `tests/contract/sim-agent-v1.json` is sim-agent's real answers, captured by `tests/contract/capture.py`
  run with a sim-agent checkout's Python (never edited by hand); `tests/test_contract.py` holds the fake
  backend and the plugin to it, and with `SIM_AGENT_PYTHON=<that python>` re-captures and fails on a change.
- Read files with EWALD's own readers (`ewald.io.FileSource`), never parse VASP here.
  Files not retrieved by AiiDA come from the cluster work directory through EWALD's SSH remote.
- Tokens only through `ewald.keychain` (never in `simagent.json`, settings.json or `os.environ`); the web
  password is used once to mint a token and never stored.
- Standard library HTTP only (no requests dependency). Import only EWALD's public surface where
  it has one (`ewald.plugin_api`, `ewald.io`, `ewald.settings.config_dir`, `ewald.keychain`).
- Tests: `pip install -e ".[test]" && pytest` against an installed EWALD (frssp/ewald); they need no
  AiiDA. Lint: `ruff check src tests`.
- The EWALD plugin index (frssp/ewald-plugins `plugins/simagent.toml`) pins a commit; merge PRs
  here with a merge commit, not squash.
