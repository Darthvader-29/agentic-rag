# M7 — Multi-Provider BYOK (Backend Phase P4)

This milestone makes the app multi-tenant *and* multi-provider on the client: an authenticated
user can store their own LLM provider keys (Bring Your Own Key — Gemini / OpenAI / Anthropic) on a
new **Settings** page, and pick a provider + model per conversation via a **model-picker** next to
the chat input. The provider/model choice rides along on the `/chat` request; the backend resolves
the user's encrypted key per request (P4 `get_llm_provider`). Everything here is behind
`NEXT_PUBLIC_FEATURE_BYOK` — with the flag off, the app behaves *exactly* like today (server default
provider, no Settings link, no model picker).

> **Status:** backend-dependent (needs **P4** provider abstraction + **P3** encrypted `user_llm_keys`
> storage) / **depends on** M6 (auth — keys are user-owned and require a Bearer token) / **unlocks**
> per-user cost attribution (cost scales with users, not with the operator's single key).
> **Flag default: OFF** (`NEXT_PUBLIC_FEATURE_BYOK=false`).

---

## 1. Objective & Scope

### In scope
- **`app/settings/page.tsx`** — a flag- and auth-gated Settings route that hosts the API-keys UI.
- **API-keys CRUD UI** (`api-keys-form` + `api-key-row`) — list the user's stored keys (masked
  metadata only), **add** a key for a provider, **rotate** (replace) it, and **delete** it.
- **Per-conversation `model-picker`** — choose `provider` + `model` (gemini / openai / anthropic)
  for the current conversation, surfaced near the chat input.
- **Sending `provider` + `model` on `/chat`** — extend the chat request payload (flag-gated) so the
  backend `get_llm_provider` resolves the right stored key.
- **Flag gating** — `NEXT_PUBLIC_FEATURE_BYOK` controls every surface above; off == today.

### Out of scope (do NOT build here)
- **Streaming changes** — SSE / token streaming is M9 (P6). The model picker only chooses
  provider + model; it does not alter the streaming vs blocking strategy.
- **Provider-specific advanced params** beyond model choice — temperature, top-p, max-tokens,
  system-prompt overrides, tool config. The backend `/chat` contract accepts only provider + model
  selection; nothing else is exposed.
- **A "default provider" server preference endpoint** — P3/P4 store a key *per provider* with a
  `(user_id, provider)` unique constraint; there is no backend "set my default provider" route. The
  client-side default is derived (see §4 decisions) and persisted locally.
- **Key validation against the provider at add-time** — the backend stores ciphertext without a live
  test call; an invalid key surfaces as a 401/402 on the next `/chat` (handled gracefully, §10).

---

## 2. Backend Contracts (P4 + P3 keys)

All contracts below are quoted from the backend phase docs. The TypeScript in §6 mirrors them
exactly. Two source docs are authoritative:

- **P3 — key storage & CRUD:** `Python-Agentic-RAG-Backend/docs/04_Phase3_User_Accounts_and_Auth.md`
  (Task 7 `auth/keys_router.py`, `user_llm_keys` table, `auth/crypto.py` Fernet, Appendix B auth
  matrix).
- **P4 — provider abstraction & per-request resolution:**
  `Python-Agentic-RAG-Backend/docs/05_Phase4_Multi_Provider_LLM_Abstraction.md`
  (provider enum, default models, `get_llm_provider`, `build_provider`).

### 2.1 Provider enum + model identifiers (P4)

The provider vocabulary is a closed set of three. Per `05_Phase4...md` §5 Task 1, `config.py`:

```python
DEFAULT_LLM_PROVIDER: Literal["gemini", "openai", "anthropic"] = "gemini"
DEFAULT_LLM_MODEL: str = "gemini-2.5-flash"
```

and the per-provider default models, from `05_Phase4...md` Appendix A ("Provider capability matrix")
and the adapter constructors (§5 Tasks 4–6):

| Provider    | Adapter default model (`__init__` default)         | Source |
|-------------|-----------------------------------------------------|--------|
| `gemini`    | `gemini-2.5-flash`                                  | Task 4 `GeminiProvider.__init__`, Appendix A |
| `openai`    | `gpt-4o-mini`                                       | Task 5 `OpenAIProvider.__init__`, Appendix A |
| `anthropic` | `claude-3-5-haiku-latest`                           | Task 6 `AnthropicProvider.__init__`, Appendix A |

> The backend accepts an arbitrary `model: str` (it is `model: Mapped[str]` on the key row / a free
> `model` argument to `build_provider`). The frontend **constrains** the choice to a curated,
> typed registry per provider (§6 Task a) so we never send a model the operator hasn't blessed; the
> registry is the single source of truth on the client and MUST be kept in sync with the backend
> Appendix A matrix. (See §10 "enum drift".)

### 2.2 User-LLM-keys CRUD (P3, `auth/keys_router.py`)

From `04_Phase3...md` §5 Task 7 and Appendix B. The router is mounted at **`/api/keys`** and every
route is behind `get_current_user` (Bearer access token required → 401 without it).

| Route                       | Method   | Body                         | Success | Response model            |
|-----------------------------|----------|------------------------------|---------|---------------------------|
| `/api/keys`                 | `POST`   | `KeyIn { provider, api_key }`| `201`   | `KeyOut`                  |
| `/api/keys/{provider}`      | `PUT`    | `KeyIn { provider, api_key }`| `200`   | `KeyOut` (rotate/replace) |
| `/api/keys/{provider}`      | `DELETE` | —                            | `204`   | (empty)                   |

**Add** (`POST /api/keys`), verbatim from Task 7:

```python
@router.post("", status_code=201, response_model=KeyOut)             # add
async def add_key(body: KeyIn, user=Depends(get_current_user),
                  db: AsyncSession = Depends(get_db_session)):
    rec = await LLMKeyRepository(db).upsert(
        user_id=user.id, provider=body.provider, ciphertext=encrypt_key(body.api_key))
    logger.info("llm_key_added", user_id=str(user.id),
                provider=body.provider, key_id=str(rec.id))          # NOTE: no api_key, no ciphertext
    return KeyOut(id=rec.id, provider=rec.provider)
```

**Rotate** (`PUT /api/keys/{provider}`) and **Delete** (`DELETE /api/keys/{provider}`):

```python
@router.put("/{provider}", response_model=KeyOut)                    # rotate
async def rotate_key(provider: str, body: KeyIn, ...):
    rec = await LLMKeyRepository(db).rotate(
        user_id=user.id, provider=provider, ciphertext=encrypt_key(body.api_key))
    return KeyOut(id=rec.id, provider=rec.provider)

@router.delete("/{provider}", status_code=204)                      # delete
async def delete_key(provider: str, ...):
    await LLMKeyRepository(db).delete(user_id=user.id, provider=provider)
```

> **Note — there is no explicit `GET /api/keys` route in the P3 Task-7 listing.** The phase doc
> shows add/rotate/delete; the client still needs to render *which* providers have a key on file. We
> treat the canonical **list** as `GET /api/keys` returning an array of masked metadata, which is the
> natural read counterpart and the shape the frontend depends on. This is the **one** place the
> frontend asks the backend to confirm/add a read route; if the backend instead returns the masked
> list as part of a `GET /api/keys`, the schema in §6 Task b already matches. **Treat this as a
> contract assumption flagged in §10.** The contract the frontend codes against:

```
GET /api/keys      → 200  [ KeyMetadata, ... ]      (array; empty array when no keys)
```

### 2.3 The masking contract (P3 — secrets are write-only on read)

This is the load-bearing security contract. From `04_Phase3...md`:

- The key is stored as **ciphertext** (Fernet token), never plaintext:
  `ciphertext: Mapped[str] = mapped_column(Text)  # Fernet token` (Task 7 model), and the Exit
  Criteria: *"Querying a `user_llm_keys` row returns a Fernet token, **not** the plaintext API key"*.
- The CRUD responses **never echo the secret**: *"responses **never** echo plaintext or ciphertext"*
  and *"`KeyOut` exposes only `id` + `provider`"* (Task 7). The add handler explicitly logs
  *"no api_key, no ciphertext"*.
- Therefore **the plaintext key is write-only**: the client sends it on add/rotate and the server
  never returns it on any read. There is no round-trip of the secret to the browser, ever.

**`KeyMetadata` (masked) — the shape the list/read returns.** The minimal backend `KeyOut` is
`{ id, provider }`. The frontend models a *superset* of masked metadata so it can render a useful row
(`provider`, optional `label`, optional `last4`, optional `created_at`) while requiring **only** the
fields the backend guarantees today (`id`, `provider`). Every extra field is **optional** in the Zod
schema (§6 Task b) so the UI degrades gracefully whether the backend returns the lean `KeyOut` or a
richer masked DTO. **The schema NEVER includes a secret/ciphertext field** — if the backend ever
sent one, we would not read it (and a test asserts the secret is never rendered, §9).

Sample masked list payload (richest form the client will accept):

```json
[
  { "id": "8f3c…", "provider": "openai",    "label": "personal",  "last4": "Ab12", "created_at": "2026-05-20T10:11:12Z" },
  { "id": "1a2b…", "provider": "anthropic", "label": null,        "last4": null,   "created_at": "2026-05-22T08:00:00Z" }
]
```

Leanest form (current `KeyOut`) the client also accepts:

```json
[ { "id": "8f3c…", "provider": "openai" } ]
```

### 2.4 Per-conversation model selection on `/chat` (P4)

P4 resolves the provider **per request** from the authenticated user's stored key. From
`05_Phase4...md` §5 Task 9 `app.py` `/api/chat`:

```python
@app.post("/api/chat")
async def chat(request: ChatRequest, provider: LLMProvider = Depends(get_llm_provider), ...):
    base_route = await route_query(provider, request.message, ...)
    answer = await generate_final_response(provider, request.message, context, final_route)
```

and `get_llm_provider` (Task 7) resolves the user's `user_llm_keys` row → decrypts → builds the
provider, using the row's `provider` / `model`, falling back to `DEFAULT_LLM_PROVIDER` /
`DEFAULT_LLM_MODEL`:

```python
return build_provider(
    row.provider or settings.DEFAULT_LLM_PROVIDER,
    api_key,
    model=row.model or settings.DEFAULT_LLM_MODEL,
)
```

**What the frontend sends.** The `/chat` request carries the existing fields plus the per-request
selection when the flag is on:

```json
{
  "message": "…",
  "session_id": "…",
  "web_search_allowed": false,
  "provider": "openai",
  "model": "gpt-4o-mini"
}
```

`provider` + `model` are **optional** on the wire — omitted when the flag is off or the user has made
no selection, in which case the backend uses the stored row / server default exactly as today. The
backend resolves the user's key for that `provider`; the frontend never sends a key on `/chat`.

> **Contract assumption (flagged in §10):** P4 Task 9 shows `request.message`, `request.session_id`,
> `request.web_search_allowed`. The `provider`/`model` fields on `ChatRequest` are the natural
> extension that lets the user override the stored row per conversation. If the backend keeps the
> selection *only* on the key row (no per-request override), the frontend still works: it sends the
> fields harmlessly (ignored extra keys) and the picker effectively mirrors the stored provider. The
> frontend codes them as optional so neither interpretation breaks.

---

## 3. Decisions & Rationale

| Decision | Rationale |
|---|---|
| **Write-only secret fields — never round-trip the key to the client.** | The P3 contract is that secrets are ciphertext-at-rest and never echoed (§2.3). The `api_key` input is *send-only*: it exists in a controlled input, is posted, then the field is cleared on success and the value is dropped. No client state, cache, or store ever holds the plaintext after submit. Display is always the masked `••••last4` derived from `KeyMetadata`, never a real key. |
| **Per-conversation model state in Zustand; persisted "default" in localStorage, not the server.** | The picker selection is *live UI state* tied to the current conversation, exactly the kind of high-frequency, ephemeral state the architecture (FRONTEND_IMPROVEMENT_PLAN "State split") puts in **Zustand**, not the Query cache. There is no backend "default provider" endpoint (§2.2 note), so the user's preferred default is derived (first provider with a key, else server default) and persisted via Zustand `persist` so it survives reloads. |
| **Optimistic-with-rollback for delete; invalidate-on-settle for add/rotate.** | Delete is unambiguous (the row is gone) → optimistic removal feels instant and rolls back on error. Add/rotate change server-derived metadata we don't fully synthesize client-side (`created_at`, `last4`), so we **invalidate** the `["llm-keys"]` query on success to refetch the authoritative masked list rather than guessing. Both paths emit a toast. |
| **Provider/model registry as a typed constant synced to the backend enum.** | `providers.registry.ts` is the single client-side source of truth: a `Provider` union (`"gemini" \| "openai" \| "anthropic"`) plus a curated per-provider model list, labels, and icons, matching `05_Phase4...md` Appendix A. The `/chat` payload and the picker both derive from it, so we never send an unknown provider/model and a backend enum change is a one-file edit. |
| **Graceful fallback to server default when no key / no selection.** | With the flag on but the user holding no key for the chosen provider, the picker hints "no key — add one in Settings" and we either disable selection of that provider or send no `provider`/`model` (server falls back to `DEFAULT_LLM_PROVIDER`). With the flag off we send neither field → behavior is byte-identical to today. |
| **All BYOK surfaces gated by `flags.byok` AND auth.** | Keys are user-owned and need a Bearer token (P3 Appendix B). The Settings route and the model picker only mount when `flags.byok && flags.auth && isAuthenticated`. The flag is the kill-switch; auth is the precondition. |

---

## 4. Current-State Snapshot

Today (pre-M6/M7 baseline, and after M1–M6 land the names below):

- **No Settings page, no provider concept on the client.** There is no `app/settings/` route, no
  `features/providers/` module, no notion of "provider" or "model" anywhere in the UI.
- **`/chat` sends only `message` / `session_id` / `web_search_allowed`.** In the prototype this is
  `services/api.ts` `sendMessage(...)` building `payload: ChatRequest` with exactly those three
  fields (`services/api.ts:46-50`), and the type is `ChatRequest { message; session_id;
  web_search_allowed }` in `types/index.ts:12-16`. After M1 this becomes
  `features/chat/api/chat.schemas.ts` (`ChatRequestSchema`) + `features/chat/api/chat.api.ts`
  (`sendChatMessage`), but the **wire shape is unchanged** — still only those three fields.
- **No auth header on requests** in the prototype; M6 adds the persisted token store and the
  `http-client` Bearer interceptor (`flags.auth`). M7 *relies on* that: the keys API and `/chat`
  send `Authorization: Bearer <access>` via the M6 interceptor.
- **The model picker has nowhere to plug in yet.** `chat-input.tsx` (prototype:
  `components/chat/chat-input.tsx`; after M1: `features/chat/components/chat-input.tsx`) owns the
  web-search toggle and send affordance — the natural mount point for the picker.

Net: M7 is purely **additive** behind a flag. Flag off = the §4 baseline, unchanged.

---

## 5. Target File Tree (delta)

```
app/
  settings/
    layout.tsx                         NEW  auth+flag guard wrapper for the settings group
    page.tsx                           NEW  renders <ApiKeysForm/>

features/providers/                    NEW feature module
  api/
    providers.registry.ts              NEW  typed Provider union + per-provider model list/labels/icons
    keys.schemas.ts                    NEW  Zod: AddKeyRequest, KeyMetadata, KeyListResponse
    keys.api.ts                        NEW  add/list/rotate/delete via authed http-client
  store/
    provider.store.ts                  NEW  Zustand: per-conversation provider+model selection (persisted)
  hooks/
    use-api-keys.ts                    NEW  TanStack Query list + add/rotate/delete mutations
    use-model-selection.ts             NEW  reads/writes provider.store; gates on owned keys
  components/
    api-keys-form.tsx                  NEW  list of provider rows + add affordance
    api-key-row.tsx                    NEW  one provider row: masked ••••last4 + rotate/delete
    provider-icon.tsx                  NEW  small icon per provider
    model-picker.tsx                   NEW  provider+model dropdown near the chat input

features/chat/
  api/chat.schemas.ts                  EDIT add optional provider+model to ChatRequestSchema
  api/chat.api.ts                      EDIT thread provider+model into the /chat payload
  components/chat-input.tsx            EDIT mount <ModelPicker/> (flag-gated)

components/layout/
  app-sidebar.tsx                      EDIT add a flag+auth-gated "Settings" link / user-menu item

lib/
  flags.ts                            EDIT add `byok` flag (reads NEXT_PUBLIC_FEATURE_BYOK)
  env.ts                              EDIT add NEXT_PUBLIC_FEATURE_BYOK to the Zod env schema

test/
  msw/handlers.ts                      EDIT add /api/keys CRUD + masked GET handlers
  features/providers/*.test.ts(x)      NEW  unit/component tests (see §9)
```

---

## 6. Tasks (ordered)

> Each task lists a goal, the files touched, and full copy-pasteable TypeScript/TSX. Types are
> `z.infer` of the Zod schemas so runtime + compile-time stay locked, matching the M1 convention.
> Assumes M1 `lib/api/http-client.ts` exposes a typed `request<T>(path, { method, body, schema, auth,
> signal })`, M0 `lib/env.ts`/`lib/flags.ts`, and M6 auth (token store + Bearer interceptor).

### Task 0 — Flag + env wiring

**Goal:** introduce `NEXT_PUBLIC_FEATURE_BYOK` and `flags.byok`; off by default.

**Files:** `lib/env.ts`, `lib/flags.ts`.

```ts
// lib/env.ts  (add to the existing Zod env schema)
// Coerce "true"/"false" strings → boolean; default false (flag ships dark).
const boolFlag = z
  .enum(["true", "false"])
  .default("false")
  .transform((v) => v === "true");

export const env = clientEnvSchema.parse({
  // …existing keys…
  NEXT_PUBLIC_FEATURE_BYOK: process.env.NEXT_PUBLIC_FEATURE_BYOK,
});
// where clientEnvSchema includes:
//   NEXT_PUBLIC_FEATURE_BYOK: boolFlag,
```

```ts
// lib/flags.ts  (add to the existing flags object)
import { env } from "@/lib/env";

export const flags = {
  // …existing: streaming, auth, presignedUpload…
  byok: env.NEXT_PUBLIC_FEATURE_BYOK,
} as const;
```

---

### Task a — `providers.registry.ts` (typed provider/model registry)

**Goal:** the single client source of truth for the provider enum and per-provider models, matching
`05_Phase4...md` Appendix A. The picker, the `/chat` payload, and the keys form all derive from this.

**Files:** `features/providers/api/providers.registry.ts`.

```ts
// features/providers/api/providers.registry.ts
import type { LucideIcon } from "lucide-react";
import { Sparkles, Bot, Brain } from "lucide-react";

/**
 * Closed provider set — MUST mirror the backend Literal in
 * Python-Agentic-RAG-Backend config.py:
 *   DEFAULT_LLM_PROVIDER: Literal["gemini", "openai", "anthropic"]
 * (05_Phase4_Multi_Provider_LLM_Abstraction.md §5 Task 1).
 */
export const PROVIDERS = ["gemini", "openai", "anthropic"] as const;
export type Provider = (typeof PROVIDERS)[number];

export interface ModelOption {
  /** Wire identifier sent to the backend (must be a model the operator supports). */
  readonly id: string;
  /** Human label for the picker. */
  readonly label: string;
}

export interface ProviderMeta {
  readonly id: Provider;
  readonly label: string;
  readonly icon: LucideIcon;
  /** Curated model list; [0] is the default and matches the adapter default in Appendix A. */
  readonly models: readonly ModelOption[];
}

/**
 * Per-provider model lists. The first entry of each is the backend adapter default
 * (05_Phase4...md Appendix A "Default model" row):
 *   gemini    → gemini-2.5-flash
 *   openai    → gpt-4o-mini
 *   anthropic → claude-3-5-haiku-latest
 * Keep IN SYNC with the backend Appendix A matrix (see milestone §10 "enum drift").
 */
export const PROVIDER_REGISTRY: Readonly<Record<Provider, ProviderMeta>> = {
  gemini: {
    id: "gemini",
    label: "Google Gemini",
    icon: Sparkles,
    models: [
      { id: "gemini-2.5-flash", label: "Gemini 2.5 Flash" },
      { id: "gemini-2.5-pro", label: "Gemini 2.5 Pro" },
    ],
  },
  openai: {
    id: "openai",
    label: "OpenAI",
    icon: Bot,
    models: [
      { id: "gpt-4o-mini", label: "GPT-4o mini" },
      { id: "gpt-4o", label: "GPT-4o" },
    ],
  },
  anthropic: {
    id: "anthropic",
    label: "Anthropic Claude",
    icon: Brain,
    models: [
      { id: "claude-3-5-haiku-latest", label: "Claude 3.5 Haiku" },
      { id: "claude-3-5-sonnet-latest", label: "Claude 3.5 Sonnet" },
    ],
  },
};

/** The server-side fallback when no selection is made (config.py DEFAULT_LLM_*). */
export const SERVER_DEFAULT = {
  provider: "gemini" as Provider,
  model: "gemini-2.5-flash",
} as const;

export const isProvider = (v: unknown): v is Provider =>
  typeof v === "string" && (PROVIDERS as readonly string[]).includes(v);

export const defaultModelFor = (p: Provider): string =>
  PROVIDER_REGISTRY[p].models[0].id;

export const providerList = (): readonly ProviderMeta[] =>
  PROVIDERS.map((p) => PROVIDER_REGISTRY[p]);
```

---

### Task b — `keys.schemas.ts` (Zod contracts)

**Goal:** Zod schemas for the add/rotate request and the masked list response. The response schema
**never** includes a secret; extra masked fields are optional so we accept either the lean `KeyOut`
or a richer masked DTO (§2.3).

**Files:** `features/providers/api/keys.schemas.ts`.

```ts
// features/providers/api/keys.schemas.ts
import { z } from "zod";
import { PROVIDERS } from "./providers.registry";

const ProviderSchema = z.enum(PROVIDERS);

/**
 * Add / rotate request body — backend KeyIn { provider, api_key }
 * (04_Phase3...md §5 Task 7). The api_key is WRITE-ONLY: sent here, never read back.
 */
export const AddKeyRequestSchema = z.object({
  provider: ProviderSchema,
  // Send-only secret. Trimmed; non-empty. Never stored in client state after submit.
  api_key: z.string().trim().min(1, "API key is required"),
});
export type AddKeyRequest = z.infer<typeof AddKeyRequestSchema>;

/**
 * Masked metadata returned on list/read. NO secret field, ever.
 * Required fields are exactly the backend KeyOut guarantee { id, provider };
 * label/last4/created_at are optional/nullable so we degrade gracefully whether the
 * backend returns lean KeyOut or a richer masked DTO (milestone §2.3).
 */
export const KeyMetadataSchema = z.object({
  id: z.string(),
  provider: ProviderSchema,
  label: z.string().nullish(),
  last4: z.string().length(4).nullish(),
  created_at: z.string().datetime().nullish(),
});
export type KeyMetadata = z.infer<typeof KeyMetadataSchema>;

/** GET /api/keys → array of masked metadata (empty array when none). */
export const KeyListResponseSchema = z.array(KeyMetadataSchema);
export type KeyListResponse = z.infer<typeof KeyListResponseSchema>;
```

---

### Task c — `keys.api.ts` (authed CRUD calls)

**Goal:** typed add/list/rotate/delete via the M1 `http-client` with `auth: true` (M6 Bearer
interceptor attaches the access token).

**Files:** `features/providers/api/keys.api.ts`.

```ts
// features/providers/api/keys.api.ts
import { request } from "@/lib/api/http-client";
import type { Provider } from "./providers.registry";
import {
  AddKeyRequestSchema,
  KeyListResponseSchema,
  KeyMetadataSchema,
  type AddKeyRequest,
  type KeyListResponse,
  type KeyMetadata,
} from "./keys.schemas";

const KEYS_PATH = "/keys"; // http-client prepends NEXT_PUBLIC_API_URL → /api/keys

/** GET /api/keys → masked list (never includes secrets). */
export async function listKeys(signal?: AbortSignal): Promise<KeyListResponse> {
  return request<KeyListResponse>(KEYS_PATH, {
    method: "GET",
    schema: KeyListResponseSchema,
    auth: true,
    signal,
  });
}

/** POST /api/keys → 201 KeyOut/KeyMetadata. body.api_key is WRITE-ONLY. */
export async function addKey(body: AddKeyRequest): Promise<KeyMetadata> {
  const parsed = AddKeyRequestSchema.parse(body);
  return request<KeyMetadata>(KEYS_PATH, {
    method: "POST",
    body: parsed,
    schema: KeyMetadataSchema,
    auth: true,
  });
}

/** PUT /api/keys/{provider} → rotate/replace. Same write-only secret. */
export async function rotateKey(body: AddKeyRequest): Promise<KeyMetadata> {
  const parsed = AddKeyRequestSchema.parse(body);
  return request<KeyMetadata>(`${KEYS_PATH}/${parsed.provider}`, {
    method: "PUT",
    body: parsed,
    schema: KeyMetadataSchema,
    auth: true,
  });
}

/** DELETE /api/keys/{provider} → 204 (no body). */
export async function deleteKey(provider: Provider): Promise<void> {
  await request<void>(`${KEYS_PATH}/${provider}`, {
    method: "DELETE",
    auth: true,
    // 204 → http-client must tolerate an empty body (no schema parse).
  });
}
```

---

### Task d — `use-api-keys.ts` (TanStack Query list + mutations)

**Goal:** a query for the masked list and add/rotate/delete mutations with cache invalidation +
toasts. Delete is optimistic with rollback; add/rotate invalidate on success.

**Files:** `features/providers/hooks/use-api-keys.ts`.

```ts
// features/providers/hooks/use-api-keys.ts
"use client";

import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { toast } from "sonner";
import { flags } from "@/lib/flags";
import { useIsAuthenticated } from "@/features/auth/hooks/use-auth"; // M6
import {
  addKey,
  deleteKey,
  listKeys,
  rotateKey,
} from "../api/keys.api";
import type { Provider } from "../api/providers.registry";
import { PROVIDER_REGISTRY } from "../api/providers.registry";
import type { AddKeyRequest, KeyListResponse, KeyMetadata } from "../api/keys.schemas";

export const LLM_KEYS_QK = ["llm-keys"] as const;

export function useApiKeys() {
  const isAuthed = useIsAuthenticated();
  const qc = useQueryClient();

  const list = useQuery<KeyListResponse>({
    queryKey: LLM_KEYS_QK,
    queryFn: ({ signal }) => listKeys(signal),
    // Only fetch when BYOK is on and the user is authenticated (keys are user-owned).
    enabled: flags.byok && isAuthed,
    staleTime: 60_000,
  });

  const invalidate = () => qc.invalidateQueries({ queryKey: LLM_KEYS_QK });

  const add = useMutation({
    mutationFn: (body: AddKeyRequest) => addKey(body),
    onSuccess: (_data, vars) => {
      invalidate(); // refetch authoritative masked list (created_at/last4 are server-derived)
      toast.success(`${PROVIDER_REGISTRY[vars.provider].label} key saved`);
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "Could not save key"),
  });

  const rotate = useMutation({
    mutationFn: (body: AddKeyRequest) => rotateKey(body),
    onSuccess: (_data, vars) => {
      invalidate();
      toast.success(`${PROVIDER_REGISTRY[vars.provider].label} key rotated`);
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "Could not rotate key"),
  });

  const remove = useMutation({
    mutationFn: (provider: Provider) => deleteKey(provider),
    // Optimistic removal with rollback.
    onMutate: async (provider) => {
      await qc.cancelQueries({ queryKey: LLM_KEYS_QK });
      const previous = qc.getQueryData<KeyListResponse>(LLM_KEYS_QK);
      qc.setQueryData<KeyListResponse>(LLM_KEYS_QK, (old) =>
        (old ?? []).filter((k) => k.provider !== provider),
      );
      return { previous };
    },
    onError: (e, _provider, ctx) => {
      if (ctx?.previous) qc.setQueryData(LLM_KEYS_QK, ctx.previous);
      toast.error(e instanceof Error ? e.message : "Could not delete key");
    },
    onSuccess: (_d, provider) =>
      toast.success(`${PROVIDER_REGISTRY[provider].label} key removed`),
    onSettled: () => invalidate(),
  });

  /** Set of providers the user currently has a key for (drives picker hints). */
  const ownedProviders = new Set<Provider>((list.data ?? []).map((k) => k.provider));
  const keyFor = (p: Provider): KeyMetadata | undefined =>
    (list.data ?? []).find((k) => k.provider === p);

  return { list, add, rotate, remove, ownedProviders, keyFor };
}
```

---

### Task e — `app/settings/page.tsx` (+ layout) — gated route

**Goal:** the Settings route, gated by flag + auth, rendering the API-keys form.

**Files:** `app/settings/layout.tsx`, `app/settings/page.tsx`.

```tsx
// app/settings/layout.tsx
import { notFound, redirect } from "next/navigation";
import { flags } from "@/lib/flags";
import { getServerAuthState } from "@/features/auth/server"; // M6 helper (token presence)

export default function SettingsLayout({ children }: { children: React.ReactNode }) {
  // Flag off → the route does not exist (404), matching "no settings page link" when OFF.
  if (!flags.byok) notFound();
  // Auth precondition — keys are user-owned; bounce anonymous users to login.
  if (!getServerAuthState().isAuthenticated) redirect("/login?next=/settings");

  return (
    <div className="mx-auto w-full max-w-2xl px-4 py-8">
      <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>
      <p className="mt-1 text-sm text-muted-foreground">
        Manage your provider API keys (Bring Your Own Key).
      </p>
      <div className="mt-8">{children}</div>
    </div>
  );
}
```

```tsx
// app/settings/page.tsx
import { ApiKeysForm } from "@/features/providers/components/api-keys-form";

export const metadata = { title: "Settings · API Keys" };

export default function SettingsPage() {
  return <ApiKeysForm />;
}
```

> If M6 does not provide a server-side auth helper, gate client-side instead: render the page inside
> a `"use client"` guard that reads `useIsAuthenticated()` and shows a "sign in to manage keys"
> empty-state. The flag check (`notFound()` when off) stays server-side either way.

---

### Task f — `api-keys-form.tsx` + `api-key-row.tsx`

**Goal:** one row per provider showing masked `••••last4` (or "Not set"), with add / rotate (replace)
/ delete. The secret input is **write-only**: it clears after submit and is never pre-filled.

**Files:** `features/providers/components/api-keys-form.tsx`,
`features/providers/components/api-key-row.tsx`, `features/providers/components/provider-icon.tsx`.

```tsx
// features/providers/components/provider-icon.tsx
import type { Provider } from "../api/providers.registry";
import { PROVIDER_REGISTRY } from "../api/providers.registry";

export function ProviderIcon({ provider, className }: { provider: Provider; className?: string }) {
  const Icon = PROVIDER_REGISTRY[provider].icon;
  return <Icon className={className ?? "h-4 w-4"} aria-hidden />;
}
```

```tsx
// features/providers/components/api-key-row.tsx
"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Loader2, Trash2 } from "lucide-react";
import { ProviderIcon } from "./provider-icon";
import { PROVIDER_REGISTRY, type Provider } from "../api/providers.registry";
import type { KeyMetadata } from "../api/keys.schemas";

interface ApiKeyRowProps {
  provider: Provider;
  /** Masked metadata if a key exists, else undefined. NEVER contains a secret. */
  meta?: KeyMetadata;
  busy: boolean;
  onSave: (provider: Provider, apiKey: string) => void; // add OR rotate (same write-only path)
  onDelete: (provider: Provider) => void;
}

export function ApiKeyRow({ provider, meta, busy, onSave, onDelete }: ApiKeyRowProps) {
  // Local, send-only secret. Cleared on submit; never seeded from `meta`.
  const [secret, setSecret] = useState("");
  const exists = Boolean(meta);
  const masked = meta?.last4 ? `••••••••${meta.last4}` : exists ? "••••••••" : "Not set";

  const submit = () => {
    const value = secret.trim();
    if (!value || busy) return;
    onSave(provider, value);
    setSecret(""); // WRITE-ONLY: drop the plaintext immediately after dispatch
  };

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-border bg-card p-4 sm:flex-row sm:items-center">
      <div className="flex min-w-40 items-center gap-2">
        <ProviderIcon provider={provider} />
        <div>
          <p className="text-sm font-medium">{PROVIDER_REGISTRY[provider].label}</p>
          <p className="font-mono text-xs text-muted-foreground">{masked}</p>
        </div>
      </div>

      <div className="flex flex-1 items-center gap-2">
        <Input
          type="password"
          value={secret}
          onChange={(e) => setSecret(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && submit()}
          placeholder={exists ? "Enter new key to rotate…" : "Paste API key…"}
          // Never let a password manager autofill or remember this field.
          autoComplete="off"
          autoCorrect="off"
          autoCapitalize="off"
          spellCheck={false}
          data-1p-ignore
          data-lpignore="true"
          aria-label={`${PROVIDER_REGISTRY[provider].label} API key`}
          disabled={busy}
        />
        <Button onClick={submit} disabled={busy || !secret.trim()} size="sm">
          {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : exists ? "Rotate" : "Add"}
        </Button>
        {exists && (
          <Button
            onClick={() => onDelete(provider)}
            disabled={busy}
            size="icon"
            variant="ghost"
            aria-label={`Delete ${PROVIDER_REGISTRY[provider].label} key`}
          >
            <Trash2 className="h-4 w-4 text-destructive" />
          </Button>
        )}
      </div>
    </div>
  );
}
```

```tsx
// features/providers/components/api-keys-form.tsx
"use client";

import { Skeleton } from "@/components/ui/skeleton";
import { useApiKeys } from "../hooks/use-api-keys";
import { providerList, type Provider } from "../api/providers.registry";
import { ApiKeyRow } from "./api-key-row";

export function ApiKeysForm() {
  const { list, add, rotate, remove, keyFor } = useApiKeys();

  const onSave = (provider: Provider, apiKey: string) => {
    const exists = Boolean(keyFor(provider));
    // Same write-only secret; add() for new, rotate() for replace.
    (exists ? rotate : add).mutate({ provider, api_key: apiKey });
  };

  if (list.isLoading) {
    return (
      <div className="space-y-3">
        {providerList().map((p) => (
          <Skeleton key={p.id} className="h-20 w-full rounded-lg" />
        ))}
      </div>
    );
  }

  if (list.isError) {
    return (
      <p className="text-sm text-destructive">
        Could not load your keys. {list.error instanceof Error ? list.error.message : ""}
      </p>
    );
  }

  const busyProvider =
    add.isPending ? add.variables?.provider :
    rotate.isPending ? rotate.variables?.provider :
    remove.isPending ? remove.variables : undefined;

  return (
    <div className="space-y-3">
      {providerList().map((p) => (
        <ApiKeyRow
          key={p.id}
          provider={p.id}
          meta={keyFor(p.id)}
          busy={busyProvider === p.id}
          onSave={onSave}
          onDelete={(provider) => remove.mutate(provider)}
        />
      ))}
      <p className="pt-2 text-xs text-muted-foreground">
        Keys are encrypted at rest on the server and are never shown again after saving. Paste a new
        key to rotate.
      </p>
    </div>
  );
}
```

---

### Task g — `provider.store.ts` + `use-model-selection.ts`

**Goal:** per-conversation selected provider+model in Zustand (persisted), defaulting to the user's
configured provider (first one with a key) else the server default; `use-model-selection` reads/writes
it and exposes whether the user has a key for the selected provider.

**Files:** `features/providers/store/provider.store.ts`,
`features/providers/hooks/use-model-selection.ts`.

```ts
// features/providers/store/provider.store.ts
"use client";

import { create } from "zustand";
import { persist } from "zustand/middleware";
import {
  SERVER_DEFAULT,
  defaultModelFor,
  isProvider,
  type Provider,
} from "../api/providers.registry";

interface ProviderState {
  /** Selected provider for the current conversation; null = use server default. */
  provider: Provider | null;
  /** Selected model id; null = provider's default model. */
  model: string | null;
  setSelection: (provider: Provider, model: string) => void;
  setProvider: (provider: Provider) => void; // resets model to that provider's default
  reset: () => void;
}

export const useProviderStore = create<ProviderState>()(
  persist(
    (set) => ({
      provider: null,
      model: null,
      setSelection: (provider, model) =>
        set({ provider: isProvider(provider) ? provider : null, model }),
      setProvider: (provider) =>
        set({ provider, model: defaultModelFor(provider) }),
      reset: () => set({ provider: null, model: null }),
    }),
    {
      name: "byok-model-selection",
      // Only persist the two scalar fields; never anything secret (there is nothing secret here).
      partialize: (s) => ({ provider: s.provider, model: s.model }),
    },
  ),
);

/** Resolve the effective wire selection, falling back to the server default. */
export const effectiveSelection = (
  provider: Provider | null,
  model: string | null,
): { provider: Provider; model: string } => {
  const p = provider ?? SERVER_DEFAULT.provider;
  return { provider: p, model: model ?? defaultModelFor(p) };
};
```

```ts
// features/providers/hooks/use-model-selection.ts
"use client";

import { useMemo } from "react";
import { flags } from "@/lib/flags";
import { useApiKeys } from "./use-api-keys";
import { useProviderStore, effectiveSelection } from "../store/provider.store";
import {
  SERVER_DEFAULT,
  defaultModelFor,
  type Provider,
} from "../api/providers.registry";

export function useModelSelection() {
  const { ownedProviders } = useApiKeys();
  const provider = useProviderStore((s) => s.provider);
  const model = useProviderStore((s) => s.model);
  const setSelection = useProviderStore((s) => s.setSelection);
  const setProvider = useProviderStore((s) => s.setProvider);

  // Derived default: first provider the user has a key for, else server default.
  const derivedDefaultProvider: Provider = useMemo(() => {
    if (provider) return provider;
    const first = [...ownedProviders][0];
    return first ?? SERVER_DEFAULT.provider;
  }, [provider, ownedProviders]);

  const selectedProvider = provider ?? derivedDefaultProvider;
  const selectedModel = model ?? defaultModelFor(selectedProvider);

  /** True when the user owns a key for the selected provider (else picker hints to Settings). */
  const hasKeyForSelected = ownedProviders.has(selectedProvider);

  /**
   * The payload fragment to merge into the /chat request.
   * Flag off → {} (today's behavior). Flag on → provider+model.
   * When the user has no key for the selection, we still send it; the backend falls back to
   * its server default if it cannot resolve a key (graceful fallback, milestone §3).
   */
  const chatPayloadFragment = (): { provider?: Provider; model?: string } => {
    if (!flags.byok) return {};
    const eff = effectiveSelection(selectedProvider, selectedModel);
    return { provider: eff.provider, model: eff.model };
  };

  return {
    selectedProvider,
    selectedModel,
    hasKeyForSelected,
    ownedProviders,
    setSelection,
    setProvider,
    chatPayloadFragment,
  };
}
```

---

### Task h — `model-picker.tsx`

**Goal:** a dropdown/command near the chat input to pick provider + model for the current
conversation, disabled/hinted when the user has no key for that provider (with a link to Settings).

**Files:** `features/providers/components/model-picker.tsx`.

```tsx
// features/providers/components/model-picker.tsx
"use client";

import Link from "next/link";
import { ChevronDown, KeyRound } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { flags } from "@/lib/flags";
import { ProviderIcon } from "./provider-icon";
import { providerList, PROVIDER_REGISTRY } from "../api/providers.registry";
import { useModelSelection } from "../hooks/use-model-selection";

export function ModelPicker() {
  const {
    selectedProvider,
    selectedModel,
    ownedProviders,
    hasKeyForSelected,
    setSelection,
  } = useModelSelection();

  // Hard gate: the picker does not exist when BYOK is off (parity with today).
  if (!flags.byok) return null;

  const selectedMeta = PROVIDER_REGISTRY[selectedProvider];
  const selectedModelLabel =
    selectedMeta.models.find((m) => m.id === selectedModel)?.label ?? selectedModel;

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="sm" className="h-8 gap-1.5 rounded-full px-2 text-xs">
          <ProviderIcon provider={selectedProvider} className="h-3.5 w-3.5" />
          <span className="max-w-32 truncate">{selectedModelLabel}</span>
          {!hasKeyForSelected && <KeyRound className="h-3 w-3 text-amber-500" aria-label="No key" />}
          <ChevronDown className="h-3 w-3 opacity-60" />
        </Button>
      </DropdownMenuTrigger>

      <DropdownMenuContent align="start" className="w-64">
        {providerList().map((p) => {
          const owned = ownedProviders.has(p.id);
          return (
            <div key={p.id}>
              <DropdownMenuLabel className="flex items-center gap-2 text-xs">
                <ProviderIcon provider={p.id} className="h-3.5 w-3.5" />
                {p.label}
                {!owned && (
                  <Link
                    href="/settings"
                    className="ml-auto text-[11px] font-normal text-muted-foreground underline hover:text-foreground"
                  >
                    Add key
                  </Link>
                )}
              </DropdownMenuLabel>
              {p.models.map((m) => (
                <DropdownMenuItem
                  key={m.id}
                  // Disable models for providers the user has no key for.
                  disabled={!owned}
                  onSelect={() => setSelection(p.id, m.id)}
                  className="pl-7 text-sm"
                >
                  <span className="flex-1">{m.label}</span>
                  {selectedProvider === p.id && selectedModel === m.id && (
                    <span aria-hidden>✓</span>
                  )}
                </DropdownMenuItem>
              ))}
              <DropdownMenuSeparator />
            </div>
          );
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
```

---

### Task i — extend chat schema/api/input to carry provider+model

**Goal:** add optional `provider`/`model` to the `/chat` payload, populated from the picker only when
the flag is on.

**Files:** `features/chat/api/chat.schemas.ts`, `features/chat/api/chat.api.ts`,
`features/chat/components/chat-input.tsx`.

```ts
// features/chat/api/chat.schemas.ts  (extend the existing ChatRequestSchema from M1)
import { z } from "zod";
import { PROVIDERS } from "@/features/providers/api/providers.registry";

export const ChatRequestSchema = z.object({
  message: z.string().min(1),
  session_id: z.string(),
  web_search_allowed: z.boolean(),
  // NEW (M7) — optional; omitted when BYOK is off or no selection is made.
  provider: z.enum(PROVIDERS).optional(),
  model: z.string().optional(),
});
export type ChatRequest = z.infer<typeof ChatRequestSchema>;
// ChatResponseSchema is unchanged from M1 (answer/route/context_count/session_id).
```

```ts
// features/chat/api/chat.api.ts  (thread the optional selection through)
import { request } from "@/lib/api/http-client";
import {
  ChatRequestSchema,
  ChatResponseSchema,
  type ChatResponse,
} from "./chat.schemas";
import type { Provider } from "@/features/providers/api/providers.registry";

export interface SendChatArgs {
  message: string;
  sessionId: string;
  webSearchAllowed: boolean;
  // NEW (M7): undefined when BYOK off / no selection.
  provider?: Provider;
  model?: string;
}

export async function sendChatMessage(
  { message, sessionId, webSearchAllowed, provider, model }: SendChatArgs,
  signal?: AbortSignal,
): Promise<ChatResponse> {
  const body = ChatRequestSchema.parse({
    message,
    session_id: sessionId,
    web_search_allowed: webSearchAllowed,
    ...(provider ? { provider } : {}), // only include when present
    ...(model ? { model } : {}),
  });
  return request<ChatResponse>("/chat", {
    method: "POST",
    body,
    schema: ChatResponseSchema,
    auth: true, // M6 — /api/chat is auth-guarded under P3
    signal,
  });
}
```

```tsx
// features/chat/components/chat-input.tsx  (mount the picker + pass selection on send)
// Only the M7-relevant deltas are shown; keep the existing input/web-search/upload UI.
import { ModelPicker } from "@/features/providers/components/model-picker";
import { useModelSelection } from "@/features/providers/hooks/use-model-selection";

export function ChatInput({ isLoading, onSend }: ChatInputProps) {
  const { chatPayloadFragment } = useModelSelection();
  // …existing input/webSearch state…

  const handleSend = () => {
    if (!input.trim() || isLoading) return;
    // chatPayloadFragment() is {} when BYOK is off → onSend behaves exactly like today.
    onSend(input, webSearch, chatPayloadFragment());
    setInput("");
  };

  return (
    <div className="p-4 bg-background border-t border-border">
      <div className="max-w-4xl mx-auto space-y-2">
        {/* …existing rounded input row… */}
        <div className="flex items-center gap-2 px-2">
          {/* Renders null when flags.byok is false → no picker, no layout change. */}
          <ModelPicker />
        </div>
      </div>
    </div>
  );
}
// NOTE: onSend's signature widens to
//   (message: string, webSearch: boolean, selection?: { provider?: Provider; model?: string }) => void
// and the useChat/useBlockingChat send action forwards `selection` into sendChatMessage(). When the
// fragment is {}, no provider/model is sent → byte-identical to the M6 payload.
```

---

### Task j — Settings link in sidebar / user menu

**Goal:** a flag- and auth-gated entry point to `/settings`.

**Files:** `components/layout/app-sidebar.tsx`.

```tsx
// components/layout/app-sidebar.tsx  (add inside the sidebar/user-menu, M7 delta only)
import Link from "next/link";
import { Settings as SettingsIcon } from "lucide-react";
import { flags } from "@/lib/flags";
import { useIsAuthenticated } from "@/features/auth/hooks/use-auth"; // M6

function SettingsLink() {
  const isAuthed = useIsAuthenticated();
  // Off OR anonymous → render nothing (no link), proving flag-off == today.
  if (!flags.byok || !isAuthed) return null;
  return (
    <Link
      href="/settings"
      className="flex items-center gap-2 rounded-md px-3 py-2 text-sm text-muted-foreground hover:bg-accent hover:text-foreground"
    >
      <SettingsIcon className="h-4 w-4" />
      Settings
    </Link>
  );
}
```

---

## 7. Security Notes (BYOK)

The masking contract is enforced **client-side too**, defense-in-depth on top of the P3 server
guarantees:

- **Plaintext key is write-only and never retained.** The `api_key` lives only in the controlled
  `ApiKeyRow` `useState` for the duration of typing; on submit it is dispatched and the field is
  **cleared immediately** (`setSecret("")`). It is never written to Zustand, never to the Query
  cache, never to `localStorage`. The Query cache (`["llm-keys"]`) holds only `KeyMetadata` (masked).
- **Never echo a secret.** `KeyMetadataSchema` has no secret field; even if the backend regressed and
  returned one, the UI renders only `provider`/`label`/`last4`. A test asserts the input value never
  appears in the DOM after submit (§9).
- **No logging.** Do not `console.log` the key, request body, or any object that could contain it.
  The keys API uses the typed `http-client`; ensure no debug interceptor stringifies bodies. The
  backend already forbids logging it (P3 add handler "no api_key, no ciphertext").
- **Browser hardening on the input:** `type="password"`, `autoComplete="off"`, `autoCorrect`/
  `autoCapitalize`/`spellCheck` off, and `data-1p-ignore` / `data-lpignore="true"` to discourage
  password-manager capture of a non-credential secret.
- **Encryption-at-rest is the backend's job** (P3 Fernet, `auth/crypto.py`); the client relies on it
  and never tries to handle ciphertext.
- **HTTPS only.** Keys (and Bearer tokens) traverse the wire only over TLS — `NEXT_PUBLIC_API_URL`
  must be `https://` in any non-local environment; the same requirement as the M6 token transport.

---

## 8. Feature-Flag Behavior Matrix

`NEXT_PUBLIC_FEATURE_BYOK` (default **false**). Proves flag-off == today.

| Surface | Flag **OFF** (default — today) | Flag **ON** (+ authenticated) |
|---|---|---|
| Settings link (sidebar/user-menu) | Not rendered (`SettingsLink` returns null) | Rendered for authenticated users |
| `/settings` route | `notFound()` (404) — route effectively doesn't exist | Renders `ApiKeysForm` (after auth redirect if anonymous) |
| `useApiKeys` list query | `enabled: false` — never fetches `/api/keys` | Fetches masked list; powers rows + picker hints |
| Model picker (`ModelPicker`) | Returns `null` — not shown near chat input | Shown; pick provider+model per conversation |
| `provider.store` persistence | Inert (never read by a visible surface) | Persists per-conversation selection across reloads |
| `/chat` payload | `{ message, session_id, web_search_allowed }` only | Same **plus** optional `provider` + `model` from the picker |
| No selection / no key | n/a (no picker) | `chatPayloadFragment()` falls back to server default; backend uses its own default key/provider |
| Net behavior | **Identical to M6** — server default provider, anonymous-safe | BYOK live end-to-end |

The single guarantee: when `flags.byok === false`, every new surface short-circuits to `null` /
`notFound()` / `enabled:false`, and `chatPayloadFragment()` returns `{}` so the `/chat` body is
byte-for-byte the M6 shape.

---

## 9. Testing & Verification

**MSW handlers** (`test/msw/handlers.ts`) — add `/api/keys` CRUD with masking:

```ts
import { http, HttpResponse } from "msw";

let keys: Array<{ id: string; provider: string; last4: string; created_at: string }> = [];

export const keysHandlers = [
  http.get("*/api/keys", () =>
    // MASKED list — no secret ever returned.
    HttpResponse.json(keys.map(({ id, provider, last4, created_at }) => ({ id, provider, last4, created_at }))),
  ),
  http.post("*/api/keys", async ({ request }) => {
    const body = (await request.json()) as { provider: string; api_key: string };
    const rec = {
      id: crypto.randomUUID(),
      provider: body.provider,
      last4: body.api_key.slice(-4), // derive masked metadata; original secret discarded
      created_at: new Date().toISOString(),
    };
    keys = [...keys.filter((k) => k.provider !== body.provider), rec];
    return HttpResponse.json({ id: rec.id, provider: rec.provider, last4: rec.last4 }, { status: 201 });
  }),
  http.put("*/api/keys/:provider", async ({ params, request }) => {
    const body = (await request.json()) as { api_key: string };
    keys = keys.map((k) =>
      k.provider === params.provider ? { ...k, last4: body.api_key.slice(-4) } : k,
    );
    const rec = keys.find((k) => k.provider === params.provider)!;
    return HttpResponse.json({ id: rec.id, provider: rec.provider, last4: rec.last4 });
  }),
  http.delete("*/api/keys/:provider", ({ params }) => {
    keys = keys.filter((k) => k.provider !== params.provider);
    return new HttpResponse(null, { status: 204 });
  }),
];
// Reset `keys = []` in an afterEach to isolate tests.
```

**Unit / component tests** (`test/features/providers/`):

1. **`api-keys-form` — add.** Type a key into the Gemini row, click Add → POST `/api/keys` fires with
   `{ provider: "gemini", api_key }`; on success the row shows `••••<last4>`; **the typed input value
   is no longer in the DOM** and the input is empty (write-only assertion).
2. **`api-keys-form` — rotate.** With an existing key, type a new value → PUT `/api/keys/{provider}`;
   masked `last4` updates; input clears.
3. **`api-keys-form` — delete.** Click delete → optimistic row removal; DELETE 204; row stays gone;
   on a forced error, the row is restored (rollback) and an error toast shows.
4. **Secret never rendered back.** After add and a list refetch, assert no element's text or value
   equals the original secret; the masked list response contains no secret field.
5. **`model-picker` selection → `/chat`.** Select OpenAI / GPT-4o → send a message → the captured
   `/chat` request body includes `provider: "openai"`, `model: "gpt-4o"`.
6. **No-key hint.** With no key for Anthropic, the Anthropic models are `disabled` and an "Add key"
   link to `/settings` is present.
7. **Flag-off hides everything.** With `NEXT_PUBLIC_FEATURE_BYOK=false`: `ModelPicker` renders null,
   `SettingsLink` renders null, `useApiKeys().list` is disabled (no fetch), and a sent `/chat` body
   has **no** `provider`/`model` keys. (Snapshot the body to prove parity with M6.)
8. **`provider.store` persistence.** `setSelection` then re-mount → selection restored from
   `localStorage` (`byok-model-selection`); `reset()` clears it.
9. **Schema guards.** `KeyMetadataSchema` rejects a payload carrying a stray `api_key`/`ciphertext`
   field only insofar as it is ignored (strip), and `AddKeyRequestSchema` rejects empty `api_key`.

**Manual (flag on, against MSW or a P4 backend):** log in → open `/settings` → add an OpenAI key →
see masked row → pick OpenAI/GPT-4o in the chat picker → send → confirm the network `/chat` body
carries `provider`/`model` and the answer renders; rotate then delete the key; toggle the flag off and
confirm the picker and Settings link vanish and `/chat` reverts to the three-field body.

**Gates:** `npm run lint`, `prettier --check`, `tsc --noEmit`, `vitest run`, `next build` all green;
CI green on the branch (same per-milestone gate as the rest of the plan).

---

## 10. Risks & Gotchas

1. **Echoing a secret.** The headline risk. Mitigation: write-only input, cleared on submit; masked
   metadata schema with no secret field; the "secret never rendered" test (§9.4). Never add a "show
   key" affordance — there is nothing to show; the plaintext is unrecoverable by design.
2. **Password-manager autofill / capture.** 1Password/LastPass may try to fill or save the key as a
   credential. Mitigation: `type="password"` + `autoComplete="off"` + `data-1p-ignore` /
   `data-lpignore="true"`; do not wrap the input in a `<form>` with a username field.
3. **Provider/model enum drift vs backend.** `providers.registry.ts` is the **single client source of
   truth** and must track `05_Phase4...md` Appendix A. If the backend adds a 4th provider or changes a
   default model, this is a one-file edit; a stale registry means the picker offers a model the
   backend rejects (→ `LLMResponseError` 502 / unknown-provider). Consider a CI check comparing the
   registry to the backend enum.
4. **`GET /api/keys` read route assumption.** P3 Task 7 lists add/rotate/delete explicitly; the
   masked **list** route is the natural read counterpart the frontend depends on (§2.2 note). If the
   backend exposes the list differently (e.g. embedded in a profile endpoint), only `keys.api.ts`
   `listKeys()` changes — the schema already matches lean or rich masked DTOs.
5. **Per-request `provider`/`model` override assumption.** If P4 resolves the provider **only** from
   the stored key row (no `/chat` override), the extra fields are harmlessly ignored and the picker
   effectively mirrors the stored provider. Coding them optional means neither interpretation breaks
   (§2.4 note). If overrides ARE honored, the user can pick any provider they have a key for per
   conversation.
6. **No key for the selected provider.** The picker disables models for unowned providers and hints
   "Add key" → `/settings`. If a user somehow selects an unowned provider (e.g. via persisted state
   after deleting a key), the backend falls back to its server default or returns `LLMAuthError`
   (401) — surfaced as a clean toast, not a crash.
7. **Per-conversation vs global default.** The selection is per-conversation UI state in Zustand and
   persisted as a single "last used" default (there is no backend default-provider endpoint). If
   product later wants distinct selections per session id, key the store by `sessionId` — the store
   shape is ready for that extension. Document the current behavior so it isn't mistaken for a bug.
8. **Query cache holds only masked data.** `["llm-keys"]` must never be hydrated with a secret. Add
   mutations invalidate rather than `setQueryData` with a synthesized secret; delete's optimistic
   update only filters masked rows.
9. **402 / 403 / 401 on invalid key or quota.** A stored-but-invalid or exhausted key surfaces on the
   next `/chat` as `LLMAuthError` (401), `LLMRateLimitError` (429), or a billing 402 from the
   provider. The M1 `http-client`/`ApiError` path renders these as toasts; the message should nudge
   the user to check or rotate the key in Settings (link in the error toast where feasible).
10. **Flag-off regressions.** Any new surface that forgets its `flags.byok` guard breaks the "off ==
    today" guarantee. The flag-off test (§9.7) snapshots the `/chat` body and asserts the picker /
    link render null as a regression tripwire.

---

## 11. Exit Criteria (checkable)

1. **Flag off == today.** With `NEXT_PUBLIC_FEATURE_BYOK=false`: no Settings link, `/settings` 404s,
   no model picker, `useApiKeys` does not fetch, and the `/chat` body is exactly
   `{ message, session_id, web_search_allowed }` (snapshot-verified vs M6).
2. **Settings page (flag on, authed).** `/settings` renders `ApiKeysForm`; anonymous users are
   redirected to login.
3. **Keys CRUD works against the P3 contract.** Add (`POST /api/keys`), rotate (`PUT
   /api/keys/{provider}`), delete (`DELETE /api/keys/{provider}` → 204) all succeed via the authed
   `http-client`; the masked list refetches and renders `••••last4`.
4. **Secret is write-only.** The plaintext key never appears in any client state, store, cache, log,
   or the DOM after submit; the input clears on success; tests §9.1/§9.4 pass.
5. **Model picker drives `/chat`.** With a key on file, selecting provider+model sends
   `provider`/`model` on `/chat` (test §9.5); unowned providers are disabled with an "Add key" hint.
6. **Registry matches backend enum.** `providers.registry.ts` providers/models match
   `05_Phase4...md` Appendix A (gemini/openai/anthropic + their default models).
7. **Graceful fallback.** No selection / no key → server default is used; invalid-key errors surface
   as clean toasts, not crashes.
8. **All gates green.** `lint`, `prettier --check`, `tsc --noEmit`, `vitest run`, `next build`, CI.

---

## 12. Commit Plan

Milestone-sized commits on the milestone branch (no `git` is run as part of writing this doc):

1. `feat(flags): add NEXT_PUBLIC_FEATURE_BYOK env + flags.byok (default off)`
2. `feat(providers): typed provider/model registry synced to backend P4 enum`
3. `feat(providers): keys Zod schemas (write-only AddKeyRequest, masked KeyMetadata)`
4. `feat(providers): authed keys API (add/list/rotate/delete) via http-client`
5. `feat(providers): useApiKeys query + mutations (optimistic delete, invalidate on add/rotate)`
6. `feat(settings): flag+auth-gated /settings route hosting ApiKeysForm`
7. `feat(providers): api-keys-form + api-key-row with write-only secret input`
8. `feat(providers): provider.store + use-model-selection (per-conversation, persisted)`
9. `feat(providers): model-picker with no-key hints linking to settings`
10. `feat(chat): carry optional provider+model on /chat (flag-gated)`
11. `feat(layout): flag+auth-gated settings link in sidebar/user-menu`
12. `test(providers): MSW keys CRUD + secret-never-echoed + picker→/chat + flag-off parity`

Each commit leaves the tree releasable; with the flag off the app is indistinguishable from M6.
```
