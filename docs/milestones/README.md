# Frontend Milestone Plans — Index

Detailed, self-contained implementation plans for each milestone defined in
[`../FRONTEND_IMPROVEMENT_PLAN.md`](../FRONTEND_IMPROVEMENT_PLAN.md). Every plan is written to be
implementable with zero extra context: it cites the current-state code by `file:line`, documents the
relevant backend API contract (cross-referenced to `Python-Agentic-RAG-Backend/docs`), provides
copy-pasteable TypeScript/TSX, and ends with checkable exit criteria and a commit plan.

## Reading order

The recommended first delivery is **M0 → M5** — all shippable against today's blocking backend.
**M6 → M9** are backend-dependent and land as the paired backend phases ship; each is dark-launched
behind a Zod-validated `NEXT_PUBLIC_FEATURE_*` flag so the flag-off path always equals today's behavior.

| Milestone | Title | Backend dep | Flag | UX change |
|-----------|-------|-------------|------|-----------|
| [M0](./M0_Tooling_and_Guardrails.md) | Tooling & Guardrails | — | — | none |
| [M1](./M1_Architecture_Refactor.md) | Architecture Refactor (parity) | — | — | none |
| [M2](./M2_Streaming_Ready_Core.md) | Streaming-Ready Core (dark) | — | `NEXT_PUBLIC_FEATURE_STREAMING` (off) | none |
| [M3](./M3_Chat_UX_Polish.md) | Chat UX Polish | — | — | yes |
| [M4](./M4_Motion_Layer.md) | Motion Layer | — | — | yes |
| [M5](./M5_Tests_E2E_Docker_CI.md) | Tests + E2E + Docker/CI | — | — | none |
| [M6](./M6_Auth_Activation.md) | Auth Activation | P3 | `NEXT_PUBLIC_FEATURE_AUTH` | gated |
| [M7](./M7_Multi_Provider_BYOK.md) | Multi-Provider BYOK | P4 | `NEXT_PUBLIC_FEATURE_BYOK` | gated |
| [M8](./M8_Presigned_Uploads_and_Status.md) | Presigned Uploads + Status | P5 | `NEXT_PUBLIC_FEATURE_PRESIGNED_UPLOAD` | gated |
| [M9](./M9_Real_SSE_and_Observability.md) | Real SSE + Observability | P6 | flips `NEXT_PUBLIC_FEATURE_STREAMING=true` | yes |

## Backend contract cross-references

Backend-dependent milestones cite the paired backend phase docs in
[`../../Python-Agentic-RAG-Backend/docs`](../../../Python-Agentic-RAG-Backend/docs). Where the
frontend plan's wording diverged from the authoritative backend contract, the milestone plan flags
the discrepancy explicitly and codes defensively. Known reconciliations surfaced during planning:

- **M2 / M9 (SSE):** the authoritative `token` payload is `{"text": ...}` (not a bare `"chunk"`
  string); the stream terminates with a typed `event: done` (and tolerates a `[DONE]` sentinel).
- **M7 (BYOK):** key CRUD is `POST/PUT/DELETE /api/keys` with ciphertext-at-rest, write-only-on-read;
  a `GET /api/keys` list route and per-request `provider`/`model` override are assumed and coded as optional.
- **M8 (uploads):** the backend Phase 5 contract is a two-step `POST /api/upload` + `POST /api/upload/confirm`
  polled via `GET /api/documents/{id}` (status `pending|processing|ready|failed`) rather than the
  plan's `/upload/status/{task_id}` wording; reconciled and assumption-flagged in the plan.

## Document structure (shared across all plans)

Objective & Scope → Decisions & Rationale → Current-State Snapshot → Target File Tree (delta) →
Tasks (ordered, with full code) → Feature-Flag/Env behavior → Testing & Verification →
Risks & Gotchas → Exit Criteria (checkable) → Commit Plan.
