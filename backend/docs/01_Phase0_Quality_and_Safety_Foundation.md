# Phase 0 — Quality & Safety Foundation

> **For the implementer:** execute the tasks **in order**; each ends with a verification that must
> pass before its commit. The tree stays releasable after every task. Companion to
> [`00_Master_Upgrade_Roadmap.md`](./00_Master_Upgrade_Roadmap.md) §4 (Phase 0).
>
> **Doc numbering:** `00` = master roadmap; `01` = this first per-phase detail doc.

## 1. Objective & scope

Establish quality/safety guardrails so every later phase is safe to merge — quality built in, not
bolted on. Phase 0 changes **no runtime behavior**; it adds tooling, structured logging, one config
source, and dependency hygiene.

**In scope:** `ruff` (lint + format), `mypy` (lenient baseline), Jenkins CI, `pre-commit`,
`structlog`, a single `pydantic-settings` `Settings`, `uv`-managed dependencies.

**Explicitly deferred** (do **not** do here): dependency injection / `lifespan`, async I/O refactor
(Phase 1); CORS tightening and auth (Phase 3); removing the unused `uploadthing` integration. The
import-time external clients (`genai.configure`, `Pinecone(...)`, `boto3.client(...)`) **stay
module-level** in Phase 0 — only their config source changes.

## 2. Decisions & rationale

| Decision | Rationale |
|---|---|
| **Ruff only** (`ruff check` + `ruff format`) | One tool replaces flake8/isort/black; Black-compatible formatter, single config, no two-formatter conflicts. (Supersedes the roadmap's literal "ruff + black".) |
| **mypy lenient, ratchet later** (`ignore_missing_imports=true`, no `strict`) | Codebase is untyped; a strict gate would block every PR. Pass at today's baseline; tighten per later phase. |
| **`pyproject.toml` = dep source of truth + `uv.lock`; `requirements.txt` generated** | `uv` resolves the full graph (fixes cross-dependency conflicts); junk vanishes by never being declared; Docker/Jenkins keep `pip install -r requirements.txt` unchanged. |
| **Jenkins, not GitHub Actions** | Per project direction (backend developed independently; frontend separate). Declarative `Jenkinsfile`; PR enforcement via multibranch + GitHub branch protection. |
| **Single `pydantic-settings` `Settings`** | Replaces scattered `os.getenv`/`load_dotenv`; fail-fast on missing required secrets; one import site. |
| **`structlog`** | Structured, JSON-in-prod logs replacing ~47 `print()` calls; foundation for Phase 7 tracing. |

## 3. Current-state snapshot (verified)

- **Flat layout**, not a pip-installable package: top-level `app.py`, `config.py`, `exceptions.py`;
  packages `components/`, `database/`, `integrations/{huggingface,s3,duckduckgo,uploadthing}`,
  `test/` (empty `__init__.py`). Imports are repo-root-relative (`import config`,
  `from components.router import route_query`). Python 3.12.6; entry `uvicorn app:app`.
- **`print()` logging:** ~47 calls across `app.py`, all four `components/*`, `database/db_manager.py`,
  `integrations/{huggingface,duckduckgo,uploadthing}/client.py`.
- **Config scatter:** `load_dotenv()`+`os.getenv` in `config.py`, `app.py`, `components/generation.py`,
  `components/preprocessing.py`, `integrations/s3/client.py`, `integrations/huggingface/client.py`,
  `integrations/uploadthing/client.py`. 8 secrets: `GOOGLE_API_KEY`, `PINECONE_API_KEY`,
  `HUGGINGFACE_TOKEN`, `AWS_REGION`, `S3_BUCKET_NAME`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`,
  `UPLOADTHING_API_KEY`. `config.py` also hardcodes `PINECONE_INDEX_NAME="rag-knowledge-base"`.
- **`requirements.txt` rot:** neuroimaging cluster (`nipype`, `nibabel`, `pyxnat`, `acres`,
  `etelemetry`, `prov`, `traits`, `looseversion`, `ci-info`, `rdflib`, `configobj`, `simplejson`);
  `pathlib==1.0.1` (a stdlib backport that breaks Py3 builds); duplicate `pymupdf`/`PyMuPDF`; wrong
  `dotenv==0.9.9` shim; abandoned `docx==0.2.4`; both `ddgs` and `duckduckgo_search` pinned.
  Missing: `pydantic-settings`, `structlog`, `pytest-cov`.
- **Tests:** 7 fully-mocked, offline pytest tests (`test/test_{router,retrieval,embeddings_gemini}.py`)
  using `pytest` + `pytest-asyncio`. No `conftest.py`, no coverage/pytest config.
- **Missing entirely:** `pyproject.toml`, `.pre-commit-config.yaml`, any CI, `.env.example`. `.env`
  is already git-ignored. `Dockerfile` present (python:3.12.6-slim); no `render.yaml`.

## 4. Risks & gotchas (with resolutions)

1. **Import-time external singletons + fail-fast Settings.** `components/router.py` calls
   `genai.configure(...)`; `components/generation.py` builds `GenerativeModel(...)` (and *free-rides*
   on router's global configure — it never configures itself); `database/db_manager.py` builds
   `Pinecone(...)`; `integrations/s3/client.py` builds `boto3.client(...)` — all at import. Once
   `Settings()` fails fast, **importing any of these (incl. pytest collection / `import app`) needs
   the 8 vars present.** Resolution: a root **`conftest.py`** injects dummy secrets *before* app
   import, and the Jenkins `environment{}` block sets the same dummies. No network at construction:
   pydantic-settings only validates; `genai.configure()` stores the key; **Pinecone v8** client is
   lazy; `boto3.client("s3", ...)` builds an object without contacting AWS. The conftest must land
   **before** the Settings refactor so the suite never goes red.
2. **mypy on a flat layout.** Targeting `.` triggers "duplicate module"/"not a package" errors.
   Resolution: `mypy_path="."`, `explicit_package_bases=true`, `namespace_packages=true`, and
   **invoke with explicit targets** (not `mypy .`). `ignore_missing_imports=true` silences absent
   third-party stubs (`google.generativeai`, `pinecone`, `boto3`, `huggingface_hub`, `fitz`,
   `duckduckgo_search`, `requests`).
3. **Coverage baseline unknown until measured.** Resolution: run `pytest --cov`, read `TOTAL`, set
   `--cov-fail-under=floor(TOTAL)`; record the integer in this doc + the `Jenkinsfile`; ratchet
   upward only.
4. **`requests` is a real runtime dep.** `integrations/uploadthing/client.py` uses `requests.post/get`
   → it **must** be declared. Same file raises `ValueError` **at import** if `UPLOADTHING_API_KEY` is
   unset (a latent landmine; not currently imported by the app) → move the guard into
   `UploadThingClient.__init__` and read the optional `settings.UPLOADTHING_API_KEY`.
5. **`docx` / `duckduckgo` import reality.** `database/doc_parser.py` does `import docx` (satisfied by
   **python-docx**, not the abandoned `docx==0.2.4`); `integrations/duckduckgo/client.py` does
   `from duckduckgo_search import DDGS` → declare `duckduckgo-search`, drop `ddgs` (do **not** swap the
   import in Phase 0; file a future migration since upstream is moving to `ddgs`).
6. **`import os` per file.** Keep in `s3/client.py` (`makedirs`/`path.join`) and `preprocessing.py`
   (`path.exists`/`remove`); **remove** the unused `import os` in `app.py` and `components/retrieval.py`
   (ruff will flag). `pandas`/`scipy`/`networkx`/`lxml`/`pillow` were transitive of nipype and are not
   imported by app code — let `uv` keep only what survives; verify `import app` post-lock.
7. **App import needs CWD = repo root.** `app.py` mounts `StaticFiles(directory="static")` at import,
   which requires `static/` to exist relative to CWD. pytest/CI run from repo root, so this holds.

## 5. Tasks (ordered)

> TDD micro-steps appear only in Task 6 (Settings) and Task 7 (structlog). Conventional-commit
> message given per task. `uv run <cmd>` runs inside the project venv.

### Task 1 — `pyproject.toml` (dependency source of truth + tool configs)
**Files:** create `pyproject.toml`.

```toml
[project]
name = "agentic-rag-backend"
version = "1.0.0"
description = "Multi-agent RAG backend (FastAPI, Pinecone, S3, Gemini)"
requires-python = ">=3.12,<3.13"          # Docker base is python:3.12.6-slim
dependencies = [
    "fastapi", "uvicorn", "python-multipart",
    "pydantic", "pydantic-settings",       # NEW: BaseSettings moved to pydantic-settings (v2)
    "python-dotenv", "structlog",          # NEW: structlog
    "google-generativeai", "pinecone", "boto3", "httpx",
    "requests",                            # required by integrations/uploadthing/client.py (gotcha 4)
    "huggingface_hub", "numpy",
    "langchain-text-splitters", "langgraph", "openai", "tenacity",
    "python-docx",                         # provides the `docx` import (NOT abandoned `docx`)
    "pymupdf",                             # provides `fitz`; single canonical spelling
    "duckduckgo-search",                   # client imports `from duckduckgo_search import DDGS`
]

[dependency-groups]
dev = ["ruff", "mypy", "pytest", "pytest-asyncio", "pytest-cov", "pre-commit"]

[tool.ruff]
target-version = "py312"
line-length = 100
src = ["."]
[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]
ignore = ["E501", "B008"]                  # E501: formatter owns width; B008: FastAPI File()/Form() defaults
[tool.ruff.lint.per-file-ignores]
"__init__.py" = ["F401"]
"test/*" = ["F401", "F811"]
[tool.ruff.format]
quote-style = "double"

[tool.mypy]
python_version = "3.12"
ignore_missing_imports = true
mypy_path = "."
explicit_package_bases = true
namespace_packages = true
follow_imports = "silent"
# NOT set in Phase 0 (ratchet later): disallow_untyped_defs, disallow_incomplete_defs, strict
[[tool.mypy.overrides]]
module = ["integrations.uploadthing.*"]
ignore_errors = true

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["test"]
addopts = "-q"

[tool.coverage.run]
branch = true
source = ["app", "config", "exceptions", "logging_config", "components", "database", "integrations"]
omit = ["test/*", "integrations/uploadthing/*"]
[tool.coverage.report]
show_missing = true
```

**Verify:** `python -c "import tomllib,pathlib; tomllib.loads(pathlib.Path('pyproject.toml').read_text())"` → exit 0; `uv --version` prints.
**Commit:** `build: add pyproject.toml as dependency + tooling source of truth`

### Task 2 — Resolve graph with uv; commit `uv.lock`; regenerate `requirements*.txt`
**Files:** create `uv.lock`, overwrite `requirements.txt`, create `requirements-dev.txt`.
```bash
uv lock
uv export --no-hashes --no-dev   -o requirements.txt
uv export --no-hashes --only-dev -o requirements-dev.txt
```
**Verify:** `requirements.txt` contains **none** of the banned names (§3 rot list); `uv sync` succeeds;
app imports under dummy env:
`GOOGLE_API_KEY=x PINECONE_API_KEY=x HUGGINGFACE_TOKEN=x AWS_REGION=us-east-1 S3_BUCKET_NAME=b AWS_ACCESS_KEY_ID=x AWS_SECRET_ACCESS_KEY=x uv run python -c "import app"` → exit 0.
**Commit:** `build: resolve deps with uv; regenerate pinned requirements (drop neuroimaging/junk)`

### Task 3 — `.gitignore` hygiene + `.env.example`
**Files:** extend `.gitignore`; create `.env.example`.
```dotenv
# Required (app fails fast at startup if any missing)
GOOGLE_API_KEY=
PINECONE_API_KEY=
HUGGINGFACE_TOKEN=
AWS_REGION=us-east-1
S3_BUCKET_NAME=
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
# Optional
UPLOADTHING_API_KEY=
PINECONE_INDEX_NAME=rag-knowledge-base
LOG_JSON=false
```
Add to `.gitignore` (if absent): `.venv/`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`,
`.coverage`, `htmlcov/`, `coverage.xml`, `tmp_uploads/` (created at runtime by `s3/client.py`).
**Verify:** `git check-ignore .env` → prints `.env`.
**Commit:** `chore: add .env.example and ignore secrets, caches, build artifacts`

### Task 4 — Format + lint the existing tree (mechanical, no behavior change)
**Files:** modify many (whitespace/imports only).
```bash
uv run ruff format .
uv run ruff check . --fix
uv run ruff check .            # "All checks passed!"
uv run ruff format --check .   # 0 files to reformat
```
Remove only imports unused **today** (e.g. `import os` in `retrieval.py`); imports that become unused
after Task 6 are cleaned there. Run the suite (offline) afterward — still green.
**Commit:** `style: apply ruff format and autofix lint across the codebase`

### Task 5 — `conftest.py` test env shim (must precede fail-fast Settings)
**Files:** create `conftest.py` at repo root.
```python
"""Inject dummy secrets BEFORE any app import.

config.Settings() and several integration modules construct external clients at import time and read
required env vars. Tests are fully mocked/offline, so we fill harmless dummies. No constructor here
performs network I/O (pydantic validates only; genai.configure stores the key; Pinecone v8 is lazy;
boto3.client builds an object without contacting AWS).
"""
import os

_DUMMY = {
    "GOOGLE_API_KEY": "test-google-key", "PINECONE_API_KEY": "test-pinecone-key",
    "HUGGINGFACE_TOKEN": "test-hf-token", "AWS_REGION": "us-east-1",
    "S3_BUCKET_NAME": "test-bucket", "AWS_ACCESS_KEY_ID": "test-akid",
    "AWS_SECRET_ACCESS_KEY": "test-secret", "PINECONE_INDEX_NAME": "rag-knowledge-base",
    "LOG_JSON": "false",
}
for _k, _v in _DUMMY.items():
    os.environ.setdefault(_k, _v)   # a real shell/.env value still wins
```
**Verify:** with no shell env set, `uv run pytest -q` → the existing 7 tests pass.
**Commit:** `test: add conftest injecting dummy secrets for offline imports`

### Task 6 — Settings loader (TDD) — rewrite `config.py` + update call sites
**Files:** rewrite `config.py`; create `test/test_config.py`; modify call sites.

**RED — `test/test_config.py`** (uses `monkeypatch.delenv` so it overrides the conftest dummies):
```python
import importlib
import pytest

REQUIRED = {"GOOGLE_API_KEY": "g", "PINECONE_API_KEY": "p", "HUGGINGFACE_TOKEN": "h",
            "AWS_REGION": "us-east-1", "S3_BUCKET_NAME": "b",
            "AWS_ACCESS_KEY_ID": "ak", "AWS_SECRET_ACCESS_KEY": "sk"}

def _fresh(monkeypatch, env):
    for k in list(REQUIRED) + ["UPLOADTHING_API_KEY", "PINECONE_INDEX_NAME", "LOG_JSON"]:
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import config; importlib.reload(config); return config

def test_loads_required(monkeypatch):
    c = _fresh(monkeypatch, REQUIRED); assert c.settings.GOOGLE_API_KEY == "g"
def test_index_name_default(monkeypatch):
    c = _fresh(monkeypatch, REQUIRED); assert c.settings.PINECONE_INDEX_NAME == "rag-knowledge-base"
def test_optionals_default(monkeypatch):
    c = _fresh(monkeypatch, REQUIRED)
    assert c.settings.UPLOADTHING_API_KEY is None and c.settings.LOG_JSON is False
def test_missing_required_raises(monkeypatch):
    bad = dict(REQUIRED); del bad["GOOGLE_API_KEY"]
    with pytest.raises(Exception): _fresh(monkeypatch, bad)
```
`uv run pytest test/test_config.py` → fails (no `settings`).

**GREEN — `config.py`:**
```python
"""Central application configuration: one pydantic-settings Settings object.

Reads env (and a local .env), fails fast if a required secret is missing. Module-level `settings`
singleton; Phase 1 moves this behind dependency injection.
"""
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=True, extra="ignore"
    )
    # Required (startup fails fast if absent)
    GOOGLE_API_KEY: str
    PINECONE_API_KEY: str
    HUGGINGFACE_TOKEN: str
    AWS_REGION: str
    S3_BUCKET_NAME: str
    AWS_ACCESS_KEY_ID: str
    AWS_SECRET_ACCESS_KEY: str
    # Optional
    UPLOADTHING_API_KEY: str | None = None
    PINECONE_INDEX_NAME: str = "rag-knowledge-base"
    LOG_JSON: bool = Field(default=False)


settings = Settings()  # raises ValidationError on missing required vars
```
> **Debatable:** `HUGGINGFACE_TOKEN` is set **required** to match "the 8"; the HF free tier works
> without it. To allow barebones local runs, change to `str | None = None` and drop it from `REQUIRED`.

`uv run pytest test/test_config.py` → passes.

**REFACTOR — update every call site (no back-compat aliases):**
- `database/db_manager.py`: `from config import settings`; `Pinecone(api_key=settings.PINECONE_API_KEY)`; `INDEX_NAME = settings.PINECONE_INDEX_NAME`.
- `components/router.py`: `from config import settings`; `genai.configure(api_key=settings.GOOGLE_API_KEY)`.
- `components/generation.py`: **add** `from config import settings` + `genai.configure(api_key=settings.GOOGLE_API_KEY)` (it currently free-rides on router); remove dead `import os` + `load_dotenv()`.
- `integrations/s3/client.py`: build boto3 from `settings.*`; **keep** `import os`; remove `load_dotenv`.
- `integrations/huggingface/client.py`: `token=settings.HUGGINGFACE_TOKEN`; remove `import os`/`load_dotenv`.
- `integrations/uploadthing/client.py`: read `settings.UPLOADTHING_API_KEY`; **move the import-time `raise ValueError` into `UploadThingClient.__init__`**; remove `load_dotenv`.
- `components/preprocessing.py`: remove `load_dotenv`; **keep** `import os`.
- `app.py`: remove `load_dotenv()` and the **unused** `import os`.

**Verify:** `uv run pytest test/test_config.py -q` green; `uv run ruff check .` passes; repo-wide search
for `os\.getenv\(` and `load_dotenv\(` → **0 matches**.
**Commit:** `feat(config): centralize settings into pydantic-settings; fail fast on missing secrets`

### Task 7 — structlog (TDD) — new `logging_config.py`, replace ~47 `print()`
**Files:** create `logging_config.py`, `test/test_logging_config.py`; modify the 8 logging files.

**RED — `test/test_logging_config.py`:**
```python
import json, structlog
from logging_config import configure_logging

def test_json_renders_json(capsys):
    configure_logging(json_logs=True)
    structlog.get_logger("t").info("hello", route="RAG", count=3)
    p = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert p["event"] == "hello" and p["route"] == "RAG" and p["level"] == "info" and "timestamp" in p

def test_console_not_json(capsys):
    configure_logging(json_logs=False)
    structlog.get_logger("t").info("hello-console")
    assert "hello-console" in capsys.readouterr().out
```
`uv run pytest test/test_logging_config.py` → fails.

**GREEN — `logging_config.py`:**
```python
"""Structured logging (structlog). Call configure_logging() once at startup.
Per module: logger = structlog.get_logger(__name__)."""
import logging, sys
import structlog


def configure_logging(json_logs: bool | None = None) -> None:
    if json_logs is None:
        from config import settings
        json_logs = settings.LOG_JSON
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=logging.INFO)
    shared = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    renderer = structlog.processors.JSONRenderer() if json_logs else structlog.dev.ConsoleRenderer(colors=False)
    structlog.configure(
        processors=[*shared, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
```
`uv run pytest test/test_logging_config.py` → passes.

**REFACTOR — wire startup + convert prints.** In `app.py` after imports:
`from logging_config import configure_logging` / `import structlog` / `configure_logging()` /
`logger = structlog.get_logger(__name__)`. Each converted module adds
`import structlog` + `logger = structlog.get_logger(__name__)`. Apply the mapping in Appendix A;
replace `preprocessing.py`'s `traceback.print_exc()` with `exc_info=True`.
**Verify:** `uv run pytest -q` green; search `\bprint\(` in `app.py`, `components/`, `database/`,
`integrations/{huggingface,duckduckgo}` → **0 matches**; `uv run ruff check .` passes (catches the now-unused `import traceback`).
**Commit:** `feat(logging): add structlog config and replace print() with structured logging`

### Task 8 — `.pre-commit-config.yaml`
```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.8.6          # pin to the ruff version uv resolved in uv.lock
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v5.0.0
    hooks:
      - id: end-of-file-fixer
      - id: trailing-whitespace
      - id: check-yaml
      - id: check-added-large-files
```
(mypy runs in CI only, to keep commits fast.) **Verify:** `uv run pre-commit run --all-files` passes.
**Commit:** `chore: add pre-commit config (ruff + hygiene hooks)`

### Task 9 — Measure coverage baseline, set the gate
```bash
uv run pytest --cov --cov-report=term-missing
```
Read `TOTAL`; set `--cov-fail-under=floor(TOTAL)` (e.g. `38`). Record the integer in this doc + the
`Jenkinsfile`, with the note: *ratchet upward only; every later phase adding code must add tests to
hold or raise it (roadmap §5).* **Verify:** `uv run pytest --cov --cov-fail-under=<floor>` → exit 0.
**Commit:** `test: record coverage baseline and document ratchet policy`

### Task 10 — `Jenkinsfile` (declarative pipeline)
```groovy
pipeline {
  agent any
  environment {                       // dummy secrets so import-time Settings()/clients don't crash
    GOOGLE_API_KEY='ci-dummy'; PINECONE_API_KEY='ci-dummy'; HUGGINGFACE_TOKEN='ci-dummy'
    AWS_REGION='us-east-1'; S3_BUCKET_NAME='ci-dummy-bucket'
    AWS_ACCESS_KEY_ID='ci-dummy'; AWS_SECRET_ACCESS_KEY='ci-dummy'
    PINECONE_INDEX_NAME='rag-knowledge-base'; LOG_JSON='true'
    UV_CACHE_DIR="${WORKSPACE}/.uv-cache"
  }
  options { timestamps(); timeout(time: 20, unit: 'MINUTES') }
  stages {
    stage('Checkout') { steps { checkout scm } }
    stage('Setup uv') { steps { sh '''
      python3.12 --version
      curl -LsSf https://astral.sh/uv/install.sh | sh
      export PATH="$HOME/.local/bin:$PATH"; uv --version
    ''' } }
    stage('Install')  { steps { sh 'export PATH="$HOME/.local/bin:$PATH"; uv sync --frozen' } }
    stage('Lint')     { steps { sh '''export PATH="$HOME/.local/bin:$PATH"
      uv run ruff check .
      uv run ruff format --check .''' } }
    stage('Type-check'){ steps { sh '''export PATH="$HOME/.local/bin:$PATH"
      uv run mypy app.py config.py exceptions.py logging_config.py components database integrations''' } }
    stage('Test')     { steps { sh '''export PATH="$HOME/.local/bin:$PATH"
      uv run pytest --cov --cov-report=xml --junitxml=junit.xml --cov-fail-under=BASELINE''' } }
  }
  post { always { junit allowEmptyResults: true, testResults: 'junit.xml' } }
}
```
Replace `BASELINE` with the Task 9 integer. (`uv sync --frozen` fails if `pyproject.toml` changed
without re-locking.) Server-side Jenkins setup → Appendix B.
**Verify (local sim):** `uv run ruff check . && uv run ruff format --check . && uv run mypy app.py config.py exceptions.py logging_config.py components database integrations && uv run pytest --cov --cov-fail-under=<BASELINE>` → all exit 0.
**Commit:** `ci: add declarative Jenkinsfile (uv, ruff, mypy, pytest+coverage gate)`

### Task 11 — mypy clean-up pass (lenient baseline)
Run `uv run mypy app.py config.py exceptions.py logging_config.py components database integrations`;
fix only what it flags (few, under the lenient baseline). If a module is hopelessly noisy, add a
`[[tool.mypy.overrides]] ignore_errors=true` block and note as tech debt. **Verify:** mypy → "Success".
**Commit:** `chore(types): satisfy mypy lenient baseline on flat layout`

## 6. Exit criteria (checkable)

1. **CI green & enforced on PRs:** `Jenkinsfile` present; multibranch job posts a **required** commit
   status; GitHub branch protection requires it; a sample PR is blocked until green.
2. **All gates pass:** `ruff check .` ("All checks passed!"), `ruff format --check .` (0 reformats),
   `mypy <targets>` ("Success"), `pytest --cov --cov-fail-under=<BASELINE>` (exit 0).
3. **No `print()`** in `app.py`/`components/`/`database/`/`integrations/{huggingface,duckduckgo}`.
4. **Single Settings source:** repo-wide `os.getenv(`/`load_dotenv(` → 0 matches; `Settings()` raises
   on a missing required secret (covered by `test_config.py`).
5. **Dependency hygiene:** `pyproject.toml` is the only hand-edited dep file; `uv.lock` committed;
   `requirements*.txt` are uv-generated and contain none of the §3 rot list; `pydantic-settings`,
   `structlog`, `pytest-cov` present; `pip install -r requirements.txt` still works in Docker.
6. **Coverage baseline recorded** (integer in doc + `Jenkinsfile`), ratchet-upward note present.
7. **Offline imports clean:** `pytest` passes with no shell env (conftest dummies; no network).
8. `pre-commit run --all-files` passes.

## Appendix A — `print()` → structlog mapping

| Pattern (examples) | Replacement |
|---|---|
| Status/progress (`[Chat]`, `[Routing]`, `[Retrieval] RAG…`, `Downloaded to temp`, `Created N chunks`, `Successfully saved…`, `Creating new Pinecone index`) | `logger.info("event", **fields)` |
| Shape/dimension/debug (`Query embedding shape:`, `Generated N embeddings (dim=…)`, `First embedding shape:`) | `logger.debug("event", dims=…)` |
| Except-block errors (`[Chat Error]`, `[Cleanup Error]`, `Ingestion Failed`, `Pinecone Delete Error`, `[DuckDuckGo Error]`, `Error listing S3 keys`) | `logger.error("event", exc_info=True)` |
| Gemini error prints in `router.py`/`generation.py` | `logger.error("gemini_api_error", component="router", http_status=…, status_name=…, message=msg)` |

Examples: `logger.info("chat_request", message_preview=request.message[:50], web_search_allowed=request.web_search_allowed, session_id=session_id)`; `logger.error("chat_failed", exc_info=True)`; `logger.debug("query_embedding", dims=len(query_embedding))`.

## Appendix B — Jenkins server-side setup (manual; outside the repo)
1. Plugins: *GitHub Branch Source*, *Pipeline*, *Pipeline: Stage View*.
2. **Multibranch Pipeline** job → this repo; credentials = GitHub token/app with repo + commit-status scope.
3. Webhook (or periodic scan) so PRs trigger builds; GitHub Branch Source auto-posts a status
   (e.g. `continuous-integration/jenkins/pr-merge`).
4. GitHub → branch protection on default branch: require that Jenkins status + require PRs.
5. Agent prereqs: `python3.12` (3.12.6) and `curl` available.

## Appendix C — Dependency decisions (keep / drop / why)
| Action | Packages | Why |
|---|---|---|
| **Drop** | nipype, nibabel, pyxnat, acres, etelemetry, prov, traits, looseversion, ci-info, rdflib, configobj, simplejson | Neuroimaging cluster + transitives; never imported. |
| **Drop** | `pathlib==1.0.1` | Stdlib backport that breaks Py3 builds. |
| **Drop** | `dotenv==0.9.9` | Wrong shim; keep only `python-dotenv`. |
| **Drop** | `docx==0.2.4` | Abandoned; `import docx` is satisfied by `python-docx`. |
| **Drop** | `ddgs` | Code imports `duckduckgo_search`; keep that. File a future migration. |
| **Collapse** | `pymupdf`/`PyMuPDF` duplicate | One canonical `pymupdf`. |
| **Add** | `pydantic-settings`, `structlog`, `pytest-cov` | Required by Phase 0. |
| **Keep (notable)** | `requests` | Used by `integrations/uploadthing/client.py`. |
