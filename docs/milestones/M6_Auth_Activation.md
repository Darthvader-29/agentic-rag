# M6 — Auth Activation (Backend Phase P3)

Activate the dormant authentication seam built into the API layer: add a self-contained `features/auth` module (register/login routes, persisted Zustand token store, TanStack mutations), flip the `lib/api/http-client.ts` interceptor from dormant to live (attach `Bearer`, `401`→refresh-once-and-retry, `403`→typed `Forbidden`), and replace today's anonymous client-generated `session_id` with a **server-owned, user-scoped** session list with resume. Everything is gated behind `NEXT_PUBLIC_FEATURE_AUTH` so that with the flag **OFF** the application behaves byte-for-byte like today's anonymous flow, and only with the flag **ON** (against a P3 backend or a mock) does login/registration, token attaching, and the user-scoped session sidebar come alive.

**Status:** backend-dependent (needs backend **P3** — [`04_Phase3_User_Accounts_and_Auth.md`](../../../Python-Agentic-RAG-Backend/docs/04_Phase3_User_Accounts_and_Auth.md) — shipped) · **Depends on:** M1 (`lib/api/http-client.ts` dormant auth interceptor seam, feature-folder + Zod + TanStack/Zustand architecture), M0 (`lib/env.ts` / `lib/flags.ts`, `app/providers.tsx`, `<Toaster/>`) · **Unlocks:** M7 (multi-provider BYOK — `/api/keys` CRUD requires the access token this milestone obtains and attaches).

**Flag default is OFF.** `NEXT_PUBLIC_FEATURE_AUTH` defaults to `false` in `lib/flags.ts` (per M0's "all forward-compat flags default `false`" rule). M6 ships **dark**: no login wall, no token attach, no auth UI on the critical path until an operator sets the flag `true`.

---

## 1. Objective & Scope

**Objective:** turn the prototype's forgeable, anonymous `session_id` model into a real multi-tenant identity model that matches backend P3, **without breaking the flag-off path**. After M6, flipping `NEXT_PUBLIC_FEATURE_AUTH=true` (and pointing at a P3 backend) gives: register → login → persisted access+refresh tokens → every protected request carries `Authorization: Bearer <access>` → transparent `401`→refresh→retry → user-scoped server-owned sessions in the sidebar with resume. Flipping it back `false` restores today's exact behavior.

**In scope**
- `features/auth/*` module: API client + Zod schemas matching P3 contracts, persisted token store, hooks, login/register/user-menu/auth-guard components.
- `app/(auth)/login/page.tsx` and `app/(auth)/register/page.tsx` route group (unauthenticated, no app chrome).
- **Activation** of the dormant interceptor in `lib/api/http-client.ts`: read flag + token, attach `Bearer`, single-flight `401`→`POST /auth/refresh`→retry-once, on refresh failure clear store + redirect `/login`, on `403` throw a typed `ApiError` with `kind: "forbidden"`.
- `features/sessions/*`: server-owned session list (TanStack Query), `session.store` for the current session, `session-list.tsx` in the sidebar with resume + new-session.
- Make `chat.api` (and upload/cleanup) send the **user-owned** session id + `Bearer` when the flag is on; fall back to the anonymous client-generated id when off.
- `auth-guard` gating the chat route when the flag is on; passthrough when off.
- Flag wiring in `lib/flags.ts` (`flags.auth`) — assumed added by M0 with default `false`; M6 *consumes* it.

**Out of scope (explicitly)**
- **BYOK key management UI** (the `settings/` route, `api-keys-form`, add/rotate/delete against `/api/keys`) → **M7**. P3 *ships* the encrypted `user_llm_keys` CRUD endpoints, but M6 only obtains and attaches the token that gates them; it renders **no** key UI.
- **Provider/model picker** (gemini/openai/anthropic) → M7 (consumes P4).
- Streaming, presigned uploads, document-status polling — unrelated phases.
- Email verification, password reset, OAuth, refresh-token rotation/reuse detection, token revocation/blocklist, rate limiting, account lockout — **all out of scope on the backend too** (P3 Appendix B "Known gaps (deferred)"; §1 "Explicitly deferred"). The frontend must not pretend these exist (e.g. no "forgot password" link that 404s).

---

## 2. Backend Auth Contract (P3)

Source of truth: [`Python-Agentic-RAG-Backend/docs/04_Phase3_User_Accounts_and_Auth.md`](../../../Python-Agentic-RAG-Backend/docs/04_Phase3_User_Accounts_and_Auth.md). All TypeScript Zod contracts below are derived from it; line citations are to that file.

### 2.1 Token model

- **Stateless JWT bearer auth** (`pyjwt`), **no server-side session store** — any instance validates any token with the shared `JWT_SECRET` (§1 "In scope" lines 24–27; §2 line 58).
- **Two tokens:** short-lived **access** + longer-lived **refresh**, distinguished by a `type` claim (`"access"` / `"refresh"`) (§2 line 59; Task 3 `_create_token`, lines 268–271; gotcha 6, lines 116–119).
- **TTLs from `Settings`** (Task 1, lines 166–168): `ACCESS_TOKEN_TTL_MINUTES = 15`, `REFRESH_TOKEN_TTL_DAYS = 7`. The frontend must assume an access token expires in **~15 minutes** and plan refresh accordingly (do not hardcode; treat `401` as the trigger, not a timer).
- **Claims** (Task 3, line 270): `{ "sub": "<user-id>", "type": "access"|"refresh", "iat": <ts>, "exp": <ts> }`. `sub` is the user UUID as a string (`create_access_token(str(user.id))`, login handler line 324). **The token is opaque to the frontend** — never decode it client-side for identity; call `GET`-user instead (see 2.4 note). Decoding carries small `leeway=10` server-side (Task 3 line 283) — relevant to clock-skew (Risk §9).
- A **stolen access token is valid until `exp`** (Appendix B "Known gaps"); the short TTL is the only mitigation. Frontend implication: keep access-token exposure minimal, clear on logout.

### 2.2 Endpoints

All under the API origin `env.NEXT_PUBLIC_API_URL`. Note the **prefix split**: auth router is mounted at `/auth` (Task 4, `APIRouter(prefix="/auth")`, line 308) and the chat/upload/cleanup routes live under `/api` (current-state §3 lines 81–82, `app.py:172/140/250`). Since `NEXT_PUBLIC_API_URL` already ends in `/api` today (`services/api.ts:5-6`), the auth endpoints are **siblings of** `/api`, i.e. at `<origin>/auth/*`, **not** `<base>/auth/*`. **This is a real path gotcha** (see Risk §11 and Task 2 `AUTH_BASE`).

| # | Route | Method | Auth | Request body | Success | Errors |
|---|---|---|---|---|---|---|
| 1 | `/auth/register` | POST | none | `{ email, username, password }` (`RegisterIn`, Task 4 line 305/334) | `201` + `UserOut` `{ id, email, username }` (router lines 310–317; `UserOut` "never `hashed_password`", line 335) | `409` duplicate email/username (line 313; Appendix B line 605) |
| 2 | `/auth/login` | POST | none | `{ email, password }` (`LoginIn`) | `200` + `TokenPair` `{ access_token, refresh_token }` (lines 319–325) | `401` invalid credentials, **generic message** (line 323; Appendix B line 606) |
| 3 | `/auth/refresh` | POST | refresh token in **body** | `{ refresh_token }` (`RefreshIn`) | `200` + `TokenPair` (fresh pair, lines 327–332) | `401`/`400` if an **access** token (wrong `type`) is sent (line 339; Appendix B line 607) |
| 4 | `/api/chat` | POST | **yes (access)** | `{ message, session_id, web_search_allowed }` | `200` `ChatResponse` | `401` unauth · `403` other user's session (Appendix B line 608; isolation Task 6 lines 400–405) |
| 5 | `/api/upload` | POST | **yes (access)** | multipart `file` + `session_id` | `200` | `401` · `403` other user's session (line 609) |
| 6 | `/api/cleanup` | POST | **yes (access)** | `{ session_id, file_keys }` | `200` | `401` · `403` other user's doc (line 610) |
| 7 | `/api/keys*` | POST/PUT/DELETE | **yes (access)** | — | — | M7 consumes; out of scope here (lines 611–613) |

> **There is no `GET /auth/me` in the P3 doc.** Task 4 implements only `register`/`login`/`refresh` (lines 294–340); the `get_current_user` dependency (Task 5) is server-internal and is **not exposed as an endpoint**. `/auth/register` returns the `UserOut` shape, and `/auth/login` returns only the `TokenPair` — **login does not echo the user object** (line 324 returns `TokenPair` only). **Consequence for the frontend:** we obtain the `User` object at **register** time; at **login** time we do not get a user object back, only tokens. Therefore the auth store derives a minimal identity from what is available — see Decision §3 and the `auth.store` note. We keep a typed `User` shape and populate it from `/auth/register`; on a login-only flow we store the email the user typed (it is the login key) and treat the user object as `null`-able. **Do not** invent a `/auth/me` call — it does not exist. (If the backend later adds one, wire `auth.api.me()` then; the seam is left in place but unused.)

### 2.3 Auth matrix & 401 vs 403 semantics

From Appendix B (lines 601–613) and §2 decision row (line 64): **`401` = "who are you?"** (missing/expired/garbage token, or wrong token `type`); **`403` = "I know who you are, you may not touch this"** (cross-user session/document access); **`404` = genuinely missing id** (not 403 — line 64, gotcha 7 lines 121–124). The interceptor must treat these distinctly: `401` triggers the refresh dance; `403` is terminal (surface "forbidden", never retry/refresh — refreshing won't change ownership); `404` is an ordinary not-found.

### 2.4 How `/chat` / `/upload` / `/cleanup` change

Today they are wide open (current-state §3, lines 81–82). After P3:
- Each requires `Authorization: Bearer <access>` (Task 5 lines 373–374). **`401` without it.**
- `session_id` becomes **owned**: on first use the backend binds the supplied `session_id` to `current_user` (`session_repo.create(id=session_id, user_id=user.id)`); a `session_id` owned by another user → **`403`** (Task 6 lines 400–405). A non-existent id → **`404`** (line 410).
- The wire shape of `ChatRequest`/`ChatResponse` is **unchanged** — `session_id` is still a field in the body; what changes is that the server now enforces ownership of it and the request must be authenticated. So the frontend's `chat.schemas` need not change; only the **source** of `session_id` (server-owned vs client-generated) and the **headers** (Bearer attached) change, both flag-gated.

### 2.5 Sample payloads

```jsonc
// POST /auth/register  →  201
// request
{ "email": "ada@example.com", "username": "ada", "password": "hunter2-long-enough" }
// response (UserOut — no hashed_password, ever)
{ "id": "6f1c…-uuid", "email": "ada@example.com", "username": "ada" }

// POST /auth/login  →  200
// request
{ "email": "ada@example.com", "password": "hunter2-long-enough" }
// response (TokenPair — NO user object)
{ "access_token": "eyJhbGciOi…", "refresh_token": "eyJhbGciOi…" }

// POST /auth/refresh  →  200   (send the REFRESH token, not the access token)
// request
{ "refresh_token": "eyJhbGciOi…" }
// response (fresh pair)
{ "access_token": "eyJhbGciOi…NEW", "refresh_token": "eyJhbGciOi…NEW" }

// POST /api/chat  →  200  (now requires: Authorization: Bearer <access>)
// request body unchanged from today
{ "message": "hi", "session_id": "server-owned-uuid", "web_search_allowed": false }
```

> **CORS:** P3 tightens CORS off `*`-with-credentials to an explicit `CORS_ALLOWED_ORIGINS` allow-list (Task 8, lines 504–522). The frontend origin (e.g. `http://localhost:3000`) **must** be in that list for credentialed/authenticated requests to work — see Risk §11.

---

## 3. Decisions & Rationale

| Decision | Choice | Rationale |
|---|---|---|
| **Token storage** | **Persisted Zustand (`persist` → `localStorage`)**, *not* httpOnly cookies | The backend is a **separate, stateless JWT API** (§2 line 58) that issues tokens in a JSON `TokenPair` body and expects `Authorization: Bearer` (Task 5, `HTTPBearer`), **not** a cookie. There is no same-site cookie session and no CSRF-token endpoint. httpOnly cookies would require the backend to set `Set-Cookie` + a CSRF strategy + same-site config — none of which P3 provides. For this SPA-against-a-separate-API topology, the Bearer-from-`localStorage` model is what the contract dictates. **Tradeoff acknowledged:** `localStorage` tokens are readable by any XSS-injected script (Risk §1). Mitigations: strict CSP, no untrusted HTML injection (we already render markdown via `react-markdown` with no `dangerouslyAllowHtml`), short access TTL (15 min), refresh-token clears on logout, and a documented upgrade path to a BFF/httpOnly-cookie proxy if/when the backend supports it. We adopt persisted Zustand because it matches the contract and the plan's stated "persisted `auth.store` tokens" (plan line 68). |
| **Refresh strategy** | **Refresh ONCE, then retry the original request once; single-flight** | A naive "refresh on every 401" loops forever if the refresh itself 401s, and N concurrent 401s would fire N parallel refreshes (a stampede) that race to overwrite each other's tokens. We use a **module-level in-flight promise**: the first 401 starts the refresh; concurrent 401s `await` the same promise; on success all retry once with the new token; on failure all reject, the store is cleared, and we redirect to `/login`. A request is retried **at most once** (a `__retried` marker prevents a second refresh on the retry's own 401). |
| **Dark-launch via flag** | **`NEXT_PUBLIC_FEATURE_AUTH`, default `false`** | First backend-dependent milestone — must ship before/independently of a deployed P3 backend without breaking today's anonymous UX (plan M6 verify line: "flag-off = today's anonymous flow"). The interceptor, guard, session-list, and Bearer-attach all branch on `flags.auth`; off ⇒ identical to today. |
| **Route group `(auth)`** | **`app/(auth)/login` + `app/(auth)/register`** | The `(auth)` group renders **without** the app sidebar/chat chrome (a dedicated minimal layout) — login/register are full-screen, no auth needed to view them. The parenthesized segment is URL-invisible: the routes are `/login` and `/register`. Matches the plan's route-group plan (plan line 51). |
| **Protecting `/chat` when flag on** | **Client-side `auth-guard`** wrapping the chat screen, **not** Next middleware | Tokens live in `localStorage`, which **middleware (edge runtime) cannot read** — middleware only sees cookies/headers. A redirect decision based on a `localStorage` token must happen client-side after hydration. So `auth-guard` is a client component that, when `flags.auth` is on, waits for store hydration, then redirects unauthenticated users to `/login`. When the flag is off it is a transparent passthrough. (We deliberately avoid middleware to prevent a hydration/edge split-brain; documented in Risk §10.) |
| **Identity source** | **`User` from `/auth/register`; email-only identity after login** | The P3 contract returns no user object from `/auth/login` and exposes no `/auth/me` (see 2.2 note). We store the `User` when registering; for a returning login we store the typed email as a lightweight identity for the user-menu avatar/initials, and leave `user` `null`-able. No fabricated `/auth/me` call. |
| **Logout** | **Clear store + clear TanStack Query cache + redirect** | Since JWT is stateless there is no server logout endpoint (none in P3). Logout is purely client-side: `auth.clear()`, `queryClient.clear()` (drop user-scoped session lists/histories so the next user can't see them), redirect to `/login`. |

---

## 4. Current-State Snapshot (with citations)

> M6 assumes M1 has landed (`lib/api/http-client.ts`, feature folders, Zod, TanStack/Zustand). The repository as inspected is still the **pre-M1 prototype**; the citations below describe today's anonymous flow that the flag-off path must preserve, and the M1 seam M6 builds on.

- **Anonymous, client-generated `session_id`.** `services/api.ts:9-17` (`getSessionId`) reads/creates a `uuid` in `localStorage` under `rag_session_id`; `services/api.ts:44` and `:81` inject it into every `/chat` and `/upload` body. `services/api.ts:74-76` persists any server-returned `session_id`. This is exactly the **forgeable, unauthenticated** model P3 closes (backend §3 lines 77–80). The flag-**off** path must keep this behavior verbatim.
- **No `Authorization` header anywhere.** `services/api.ts:52-56`, `:86-89`, `:24-31` send only `Content-Type`/`FormData` — no Bearer. After M1 this moves into `http-client.ts`; the interceptor seam is **dormant**.
- **Dormant interceptor seam.** Per plan lines 80–82, M1's `lib/api/http-client.ts` carries a typed `request<T>(path,{method,body,schema,auth,signal})` with an **auth interceptor dormant until the P3 flag**: "attach `Bearer`, on `401` refresh-once-and-retry, on `403` surface forbidden." M6 **activates** it. (In the prototype this logic does not yet exist; M6 writes it into the M1 `http-client`.)
- **`env`/`flags` exist (M0).** `lib/env.ts` (Zod-validated env, including `NEXT_PUBLIC_API_URL`) and `lib/flags.ts` (typed booleans, all forward-compat flags default `false`). M6 consumes `flags.auth` (`NEXT_PUBLIC_FEATURE_AUTH`).
- **No auth UI / no routes / no user-scoped sessions.** `app/` has only `layout.tsx` + `page.tsx` (`ls app/`). No `(auth)` group, no `features/auth`, no `features/sessions`, no session list — the sidebar (`components/chat/sidebar.tsx`, mounted at `app/page.tsx:113`) only offers "clear session" (`app/page.tsx:98-102` → `api.clearSession`).
- **Wire shapes today.** `types/index.ts:12-23`: `ChatRequest { message, session_id, web_search_allowed }`, `ChatResponse { answer, route, context_count, session_id }`. These are **unchanged** by P3 (backend 2.4) — only auth + ownership are layered on.

---

## 5. Target File Tree (delta)

Files **added** (➕) or **changed** (✏️) by M6. Nothing is deleted.

```
typescript-agentic-rag-frontend/
├── app/
│   ├── (auth)/
│   │   ├── layout.tsx                         ➕ minimal centered layout, no app chrome
│   │   ├── login/page.tsx                     ➕ login route  → /login
│   │   └── register/page.tsx                  ➕ register route → /register
│   ├── providers.tsx                          ✏️ ensure QueryClientProvider present (M1) — no change if already there
│   └── page.tsx                               ✏️ wrap chat screen in <AuthGuard> (flag-gated)
├── lib/
│   ├── api/http-client.ts                     ✏️ ACTIVATE auth interceptor (Bearer + 401→refresh→retry + 403)
│   └── api/api-error.ts                       ✏️ add ApiError.kind ("unauthorized"|"forbidden"|"network"|"http"|"parse")
├── features/
│   ├── auth/
│   │   ├── api/auth.api.ts                    ➕ register/login/refresh (+ unused me() seam)
│   │   ├── api/auth.schemas.ts               ➕ Zod: RegisterRequest/LoginRequest/RefreshRequest/TokenPair/User
│   │   ├── store/auth.store.ts               ➕ persisted Zustand: accessToken/refreshToken/user + setTokens/clear/hydration
│   │   ├── hooks/use-auth.ts                  ➕ selector facade (isAuthenticated, tokens, user, logout)
│   │   ├── hooks/use-login.ts                 ➕ TanStack mutation → setTokens → redirect
│   │   ├── hooks/use-register.ts              ➕ TanStack mutation → (optionally auto-login) → redirect
│   │   └── components/
│   │       ├── login-form.tsx                ➕ controlled + Zod, error display, toast
│   │       ├── register-form.tsx             ➕ controlled + Zod, error display, toast
│   │       ├── user-menu.tsx                 ➕ avatar + logout (sidebar)
│   │       └── auth-guard.tsx                ➕ flag-gated client guard for protected routes
│   └── sessions/
│       ├── api/sessions.api.ts               ➕ list/create/get-history (server-owned)
│       ├── store/session.store.ts            ➕ currentSessionId (+ anonymous fallback bridge)
│       ├── hooks/use-sessions.ts             ➕ TanStack Query (list) + create/resume mutations
│       └── components/session-list.tsx       ➕ sidebar list + resume + new-session
├── components/
│   └── layout/app-sidebar.tsx                ✏️ mount <SessionList/> + <UserMenu/> when flag on
├── features/chat/api/chat.api.ts              ✏️ session_id source + Bearer (flag-gated)
└── test/
    ├── msw/handlers.auth.ts                   ➕ MSW: /auth/* + /api/sessions* + 401/403 cases
    ├── auth/http-client.refresh.test.ts       ➕ single-flight 401→refresh→retry; refresh-fail→logout
    ├── auth/auth.store.test.ts                ➕ persistence + hydration
    ├── auth/auth-guard.test.tsx               ➕ redirect when flag on + unauth; passthrough when off
    └── auth/login-form.test.tsx               ➕ validation + submit + error
```

> If M1 named the sidebar differently (the prototype has `components/chat/sidebar.tsx`; the plan calls it `components/layout/app-sidebar.tsx`), apply the session-list/user-menu mount to whichever exists. This doc uses the plan's `app-sidebar.tsx` name.

---

## 6. Tasks (ordered)

Each task: **Goal**, **Files**, **full copy-pasteable code**. Implement in order — later tasks import earlier ones. TS strict; no `any`.

### Task 1 — `auth.schemas.ts`: Zod contracts matching P3

**Goal:** runtime + compile-time-locked contracts for register/login/refresh/token-pair/user, exactly matching backend P3 §2.2.
**Files:** `features/auth/api/auth.schemas.ts`

```ts
// features/auth/api/auth.schemas.ts
import { z } from "zod";

// --- Requests (match RegisterIn / LoginIn / RefreshIn, P3 Task 4) ---
export const RegisterRequestSchema = z.object({
  email: z.string().email(),
  username: z.string().min(3).max(64),
  // bcrypt truncates at 72 bytes (P3 gotcha 3). Enforce a sane lower bound only;
  // do NOT pre-hash. Cap length to keep well clear of surprises.
  password: z.string().min(8).max(72),
});
export type RegisterRequest = z.infer<typeof RegisterRequestSchema>;

export const LoginRequestSchema = z.object({
  email: z.string().email(),
  password: z.string().min(1), // backend returns generic 401; don't over-validate here
});
export type LoginRequest = z.infer<typeof LoginRequestSchema>;

export const RefreshRequestSchema = z.object({
  refresh_token: z.string().min(1),
});
export type RefreshRequest = z.infer<typeof RefreshRequestSchema>;

// --- Responses ---
// UserOut: exactly { id, email, username } — never hashed_password (P3 Task 4, line 335)
export const UserSchema = z.object({
  id: z.string().uuid(),
  email: z.string().email(),
  username: z.string(),
});
export type User = z.infer<typeof UserSchema>;

// TokenPair: { access_token, refresh_token } (P3 Task 4, lines 324/332)
export const TokenPairSchema = z.object({
  access_token: z.string().min(1),
  refresh_token: z.string().min(1),
});
export type TokenPair = z.infer<typeof TokenPairSchema>;
```

### Task 2 — `auth.api.ts`: register / login / refresh via http-client

**Goal:** typed auth calls. Note the **prefix split** (backend §2.2): auth lives at `<origin>/auth/*`, a *sibling* of `/api`, while `NEXT_PUBLIC_API_URL` ends in `/api`. We derive `AUTH_BASE` by stripping a trailing `/api`.
**Files:** `features/auth/api/auth.api.ts`

```ts
// features/auth/api/auth.api.ts
import { env } from "@/lib/env";
import { request } from "@/lib/api/http-client";
import {
  RegisterRequest,
  LoginRequest,
  RefreshRequest,
  TokenPairSchema,
  TokenPair,
  UserSchema,
  User,
} from "./auth.schemas";

// NEXT_PUBLIC_API_URL ends in "/api" (e.g. https://host/api). Auth router is mounted
// at "/auth" (sibling of "/api"), so strip a trailing "/api" to reach the origin.
function authUrl(path: string): string {
  const origin = env.NEXT_PUBLIC_API_URL.replace(/\/api\/?$/, "");
  return `${origin}${path}`;
}

export const authApi = {
  register: (body: RegisterRequest): Promise<User> =>
    request<User>(authUrl("/auth/register"), {
      method: "POST",
      body,
      schema: UserSchema,
      auth: false, // public endpoint — never attach a (nonexistent) token
      absoluteUrl: true, // http-client must NOT re-prepend the base
    }),

  login: (body: LoginRequest): Promise<TokenPair> =>
    request<TokenPair>(authUrl("/auth/login"), {
      method: "POST",
      body,
      schema: TokenPairSchema,
      auth: false,
      absoluteUrl: true,
    }),

  // Used ONLY by the interceptor's single-flight refresh (Task 4). auth:false so the
  // interceptor never tries to attach/refresh while refreshing (no recursion).
  refresh: (body: RefreshRequest): Promise<TokenPair> =>
    request<TokenPair>(authUrl("/auth/refresh"), {
      method: "POST",
      body,
      schema: TokenPairSchema,
      auth: false,
      absoluteUrl: true,
    }),

  // SEAM ONLY — P3 exposes no /auth/me (see contract §2.2). Left for a future backend.
  // Do NOT call this in M6.
  me: (): Promise<User> =>
    request<User>(authUrl("/auth/me"), {
      method: "GET",
      schema: UserSchema,
      auth: true,
      absoluteUrl: true,
    }),
};
```

> If M1's `request()` does not yet support `absoluteUrl`, add it: when `absoluteUrl` is truthy, skip the `env.NEXT_PUBLIC_API_URL` prepend and use `path` verbatim. (Trivial one-line branch in the URL-building step.)

### Task 3 — `auth.store.ts`: persisted, hydration-safe token store

**Goal:** persisted Zustand holding `accessToken`/`refreshToken`/`user`, actions `setTokens`/`setUser`/`clear`, plus a hydration flag so the guard can wait for rehydration before deciding to redirect.
**Files:** `features/auth/store/auth.store.ts`

```ts
// features/auth/store/auth.store.ts
import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";
import type { TokenPair, User } from "@/features/auth/api/auth.schemas";

interface AuthState {
  accessToken: string | null;
  refreshToken: string | null;
  user: User | null;
  /** email typed at login when no user object is returned (contract §2.2) */
  email: string | null;
  /** true once persist has rehydrated from storage; guard waits on this */
  hasHydrated: boolean;

  setTokens: (tokens: TokenPair) => void;
  setUser: (user: User | null) => void;
  setEmail: (email: string | null) => void;
  clear: () => void;
  setHasHydrated: (v: boolean) => void;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      accessToken: null,
      refreshToken: null,
      user: null,
      email: null,
      hasHydrated: false,

      setTokens: ({ access_token, refresh_token }) =>
        set({ accessToken: access_token, refreshToken: refresh_token }),
      setUser: (user) => set({ user }),
      setEmail: (email) => set({ email }),
      clear: () =>
        set({ accessToken: null, refreshToken: null, user: null, email: null }),
      setHasHydrated: (v) => set({ hasHydrated: v }),
    }),
    {
      name: "rag_auth",
      storage: createJSONStorage(() => localStorage),
      // never persist the hydration flag itself
      partialize: (s) => ({
        accessToken: s.accessToken,
        refreshToken: s.refreshToken,
        user: s.user,
        email: s.email,
      }),
      onRehydrateStorage: () => (state) => {
        state?.setHasHydrated(true);
      },
    },
  ),
);

// Non-React accessors for the interceptor (Task 4), which runs outside React.
export const authStore = {
  getAccessToken: () => useAuthStore.getState().accessToken,
  getRefreshToken: () => useAuthStore.getState().refreshToken,
  setTokens: (t: TokenPair) => useAuthStore.getState().setTokens(t),
  clear: () => useAuthStore.getState().clear(),
};
```

### Task 4 — Activate the http-client auth interceptor (single-flight refresh)

**Goal:** when `flags.auth` is on, attach `Authorization: Bearer <access>` to `auth:true` requests; on `401`, run a **single-flight** refresh exactly once and retry the original request once; on refresh failure, clear the store and redirect to `/login`; on `403`, throw a typed `ApiError({ kind: "forbidden" })`. When `flags.auth` is off, the request path is byte-for-byte today's (no Bearer, no refresh).
**Files:** `lib/api/http-client.ts`, `lib/api/api-error.ts`

```ts
// lib/api/api-error.ts (additions)
export type ApiErrorKind =
  | "unauthorized" // 401, refresh exhausted
  | "forbidden"    // 403, cross-user (terminal — never retried)
  | "http"         // other non-2xx
  | "network"      // fetch threw
  | "parse";       // Zod parse failed

export class ApiError extends Error {
  constructor(
    public readonly kind: ApiErrorKind,
    public readonly status: number | null,
    message: string,
    public readonly detail?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
  get isForbidden() {
    return this.kind === "forbidden";
  }
}
```

```ts
// lib/api/http-client.ts — auth interceptor activation (excerpt; merge into M1's request<T>)
import { z } from "zod";
import { env } from "@/lib/env";
import { flags } from "@/lib/flags";
import { ApiError } from "./api-error";
import { authStore } from "@/features/auth/store/auth.store";
import { authApi } from "@/features/auth/api/auth.api";

export interface RequestOptions<T> {
  method?: "GET" | "POST" | "PUT" | "DELETE";
  body?: unknown;
  schema?: z.ZodType<T>;
  auth?: boolean;        // attach Bearer when flags.auth && token present
  absoluteUrl?: boolean; // skip base-URL prepend (Task 2)
  signal?: AbortSignal;
  __retried?: boolean;   // internal: guards against a second refresh on the retry
}

// ---- single-flight refresh ----
let refreshInFlight: Promise<string> | null = null;

async function refreshAccessToken(): Promise<string> {
  if (refreshInFlight) return refreshInFlight; // join the in-flight refresh

  const refreshToken = authStore.getRefreshToken();
  if (!refreshToken) {
    // no refresh token ⇒ cannot recover
    return Promise.reject(
      new ApiError("unauthorized", 401, "no refresh token"),
    );
  }

  refreshInFlight = authApi
    .refresh({ refresh_token: refreshToken })
    .then((pair) => {
      authStore.setTokens(pair); // persist the fresh pair (rotated by backend)
      return pair.access_token;
    })
    .finally(() => {
      refreshInFlight = null; // clear the gate whether it resolved or rejected
    });

  return refreshInFlight;
}

function redirectToLogin() {
  if (typeof window !== "undefined") {
    const next = encodeURIComponent(window.location.pathname);
    window.location.assign(`/login?next=${next}`);
  }
}

function buildUrl(path: string, absolute?: boolean): string {
  return absolute ? path : `${env.NEXT_PUBLIC_API_URL}${path}`;
}

export async function request<T>(
  path: string,
  opts: RequestOptions<T> = {},
): Promise<T> {
  const { method = "GET", body, schema, auth, absoluteUrl, signal } = opts;
  const headers: Record<string, string> = {};
  const isForm = typeof FormData !== "undefined" && body instanceof FormData;
  if (body !== undefined && !isForm) headers["Content-Type"] = "application/json";

  // Attach Bearer ONLY when the auth feature is on. Flag off ⇒ exactly today's request.
  if (flags.auth && auth) {
    const token = authStore.getAccessToken();
    if (token) headers["Authorization"] = `Bearer ${token}`;
  }

  let res: Response;
  try {
    res = await fetch(buildUrl(path, absoluteUrl), {
      method,
      headers,
      body: body === undefined ? undefined : isForm ? (body as FormData) : JSON.stringify(body),
      signal,
    });
  } catch (e) {
    throw new ApiError("network", null, (e as Error).message ?? "network error");
  }

  // ---- 403: terminal (refreshing won't change ownership) ----
  if (res.status === 403) {
    throw new ApiError("forbidden", 403, "You do not have access to this resource.");
  }

  // ---- 401: single-flight refresh-once-and-retry (only when auth is live) ----
  if (res.status === 401 && flags.auth && auth && !opts.__retried) {
    try {
      await refreshAccessToken();
    } catch {
      authStore.clear();
      redirectToLogin();
      throw new ApiError("unauthorized", 401, "Session expired. Please sign in again.");
    }
    // retry ONCE with the refreshed token; __retried prevents a refresh loop
    return request<T>(path, { ...opts, __retried: true });
  }

  if (!res.ok) {
    let detail: unknown;
    try {
      detail = await res.json();
    } catch {
      /* non-JSON body */
    }
    if (res.status === 401) {
      authStore.clear();
      redirectToLogin();
      throw new ApiError("unauthorized", 401, "Session expired. Please sign in again.", detail);
    }
    throw new ApiError("http", res.status, `Request failed: ${res.status}`, detail);
  }

  const data = res.status === 204 ? undefined : await res.json();
  if (schema) {
    const parsed = schema.safeParse(data);
    if (!parsed.success) {
      throw new ApiError("parse", res.status, "Response failed validation", parsed.error.format());
    }
    return parsed.data;
  }
  return data as T;
}
```

> **Why this is correct and loop-free:** (1) the `__retried` marker means a request can trigger at most one refresh + one retry; if the retry also 401s, we fall to the `!res.ok` 401 branch → clear + redirect (no third attempt). (2) `refreshInFlight` makes N concurrent 401s share **one** refresh call (no stampede, no token-overwrite race). (3) `/auth/refresh` is called with `auth:false`/`absoluteUrl`, so it never re-enters the Bearer/refresh logic. (4) Everything is wrapped in `if (flags.auth && auth)` — with the flag off, none of this runs.

### Task 5 — `use-login` / `use-register` TanStack mutations + redirect

**Goal:** mutations that call the API, write the store, toast, and navigate.
**Files:** `features/auth/hooks/use-login.ts`, `features/auth/hooks/use-register.ts`, `features/auth/hooks/use-auth.ts`

```ts
// features/auth/hooks/use-auth.ts
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { useAuthStore } from "@/features/auth/store/auth.store";

export function useAuth() {
  const router = useRouter();
  const qc = useQueryClient();
  const accessToken = useAuthStore((s) => s.accessToken);
  const user = useAuthStore((s) => s.user);
  const email = useAuthStore((s) => s.email);
  const hasHydrated = useAuthStore((s) => s.hasHydrated);
  const clear = useAuthStore((s) => s.clear);

  return {
    isAuthenticated: Boolean(accessToken),
    user,
    email,
    hasHydrated,
    logout: () => {
      clear();
      qc.clear(); // drop user-scoped session lists/histories
      router.replace("/login");
    },
  };
}
```

```ts
// features/auth/hooks/use-login.ts
import { useMutation } from "@tanstack/react-query";
import { useRouter, useSearchParams } from "next/navigation";
import { toast } from "sonner";
import { authApi } from "@/features/auth/api/auth.api";
import { useAuthStore } from "@/features/auth/store/auth.store";
import type { LoginRequest } from "@/features/auth/api/auth.schemas";
import { ApiError } from "@/lib/api/api-error";

export function useLogin() {
  const router = useRouter();
  const params = useSearchParams();
  const setTokens = useAuthStore((s) => s.setTokens);
  const setEmail = useAuthStore((s) => s.setEmail);

  return useMutation({
    mutationFn: (body: LoginRequest) => authApi.login(body),
    onSuccess: (tokens, vars) => {
      setTokens(tokens);
      setEmail(vars.email); // contract returns no user object; keep email as identity
      toast.success("Signed in");
      router.replace(params.get("next") ?? "/");
    },
    onError: (err) => {
      const msg =
        err instanceof ApiError && err.status === 401
          ? "Invalid email or password."
          : "Sign-in failed. Please try again.";
      toast.error(msg);
    },
  });
}
```

```ts
// features/auth/hooks/use-register.ts
import { useMutation } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { authApi } from "@/features/auth/api/auth.api";
import { useAuthStore } from "@/features/auth/store/auth.store";
import type { RegisterRequest } from "@/features/auth/api/auth.schemas";
import { ApiError } from "@/lib/api/api-error";

export function useRegister() {
  const router = useRouter();
  const setUser = useAuthStore((s) => s.setUser);
  const setTokens = useAuthStore((s) => s.setTokens);
  const setEmail = useAuthStore((s) => s.setEmail);

  return useMutation({
    mutationFn: async (body: RegisterRequest) => {
      const user = await authApi.register(body); // 201 → UserOut
      // register does NOT return tokens; immediately log in to obtain them.
      const tokens = await authApi.login({ email: body.email, password: body.password });
      return { user, tokens, email: body.email };
    },
    onSuccess: ({ user, tokens, email }) => {
      setUser(user);
      setTokens(tokens);
      setEmail(email);
      toast.success("Account created");
      router.replace("/");
    },
    onError: (err) => {
      const msg =
        err instanceof ApiError && err.status === 409
          ? "That email or username is already taken."
          : "Registration failed. Please try again.";
      toast.error(msg);
    },
  });
}
```

### Task 6 — `(auth)/login` + `(auth)/register` pages + forms

**Goal:** full-screen, chrome-less auth routes with controlled + Zod-validated forms, inline errors, toasts.
**Files:** `app/(auth)/layout.tsx`, `app/(auth)/login/page.tsx`, `app/(auth)/register/page.tsx`, `features/auth/components/login-form.tsx`, `features/auth/components/register-form.tsx`

```tsx
// app/(auth)/layout.tsx
export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-4">
      <div className="w-full max-w-sm">{children}</div>
    </div>
  );
}
```

```tsx
// app/(auth)/login/page.tsx
import { LoginForm } from "@/features/auth/components/login-form";
export default function LoginPage() {
  return <LoginForm />;
}
```

```tsx
// app/(auth)/register/page.tsx
import { RegisterForm } from "@/features/auth/components/register-form";
export default function RegisterPage() {
  return <RegisterForm />;
}
```

```tsx
// features/auth/components/login-form.tsx
"use client";
import { useState } from "react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useLogin } from "@/features/auth/hooks/use-login";
import { LoginRequestSchema } from "@/features/auth/api/auth.schemas";

export function LoginForm() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const login = useLogin();

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const parsed = LoginRequestSchema.safeParse({ email, password });
    if (!parsed.success) {
      setError("Enter a valid email and password.");
      return;
    }
    setError(null);
    login.mutate(parsed.data);
  };

  return (
    <form onSubmit={onSubmit} className="space-y-4" noValidate>
      <h1 className="text-xl font-semibold">Sign in</h1>
      <div className="space-y-2">
        <Input
          type="email"
          placeholder="Email"
          autoComplete="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          aria-label="Email"
        />
        <Input
          type="password"
          placeholder="Password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          aria-label="Password"
        />
      </div>
      {error && <p className="text-sm text-destructive">{error}</p>}
      <Button type="submit" className="w-full" disabled={login.isPending}>
        {login.isPending ? "Signing in…" : "Sign in"}
      </Button>
      <p className="text-center text-sm text-muted-foreground">
        No account?{" "}
        <Link href="/register" className="underline">
          Create one
        </Link>
      </p>
    </form>
  );
}
```

```tsx
// features/auth/components/register-form.tsx
"use client";
import { useState } from "react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useRegister } from "@/features/auth/hooks/use-register";
import { RegisterRequestSchema } from "@/features/auth/api/auth.schemas";

export function RegisterForm() {
  const [email, setEmail] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const register = useRegister();

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const parsed = RegisterRequestSchema.safeParse({ email, username, password });
    if (!parsed.success) {
      setError(parsed.error.issues[0]?.message ?? "Check your details.");
      return;
    }
    setError(null);
    register.mutate(parsed.data);
  };

  return (
    <form onSubmit={onSubmit} className="space-y-4" noValidate>
      <h1 className="text-xl font-semibold">Create account</h1>
      <div className="space-y-2">
        <Input placeholder="Email" type="email" autoComplete="email"
          value={email} onChange={(e) => setEmail(e.target.value)} aria-label="Email" />
        <Input placeholder="Username" autoComplete="username"
          value={username} onChange={(e) => setUsername(e.target.value)} aria-label="Username" />
        <Input placeholder="Password (min 8)" type="password" autoComplete="new-password"
          value={password} onChange={(e) => setPassword(e.target.value)} aria-label="Password" />
      </div>
      {error && <p className="text-sm text-destructive">{error}</p>}
      <Button type="submit" className="w-full" disabled={register.isPending}>
        {register.isPending ? "Creating…" : "Create account"}
      </Button>
      <p className="text-center text-sm text-muted-foreground">
        Have an account?{" "}
        <Link href="/login" className="underline">
          Sign in
        </Link>
      </p>
    </form>
  );
}
```

### Task 7 — `auth-guard`: flag-gated route protection

**Goal:** when `flags.auth` is on, redirect unauthenticated users to `/login` (after store hydration); when off, render children untouched.
**Files:** `features/auth/components/auth-guard.tsx`

```tsx
// features/auth/components/auth-guard.tsx
"use client";
import { useEffect } from "react";
import { useRouter, usePathname } from "next/navigation";
import { flags } from "@/lib/flags";
import { useAuth } from "@/features/auth/hooks/use-auth";

export function AuthGuard({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const { isAuthenticated, hasHydrated } = useAuth();

  useEffect(() => {
    if (!flags.auth) return; // flag off ⇒ never guard (today's behavior)
    if (!hasHydrated) return; // wait for localStorage rehydration before deciding
    if (!isAuthenticated) {
      router.replace(`/login?next=${encodeURIComponent(pathname)}`);
    }
  }, [hasHydrated, isAuthenticated, pathname, router]);

  // Flag off: passthrough. Flag on: render children only when authenticated &
  // hydrated; otherwise render nothing (avoids a flash of protected content).
  if (!flags.auth) return <>{children}</>;
  if (!hasHydrated) return null;
  if (!isAuthenticated) return null;
  return <>{children}</>;
}
```

Wire it in `app/page.tsx` around the chat screen:

```tsx
// app/page.tsx (delta)
import { AuthGuard } from "@/features/auth/components/auth-guard";
// ...
return (
  <AuthGuard>
    {/* existing chat screen JSX */}
  </AuthGuard>
);
```

### Task 8 — Sessions: server-owned list + resume

**Goal:** when the flag is on, the sidebar shows the user's server-owned sessions (TanStack Query), supports **resume** (load a session's history into the chat store) and **new session**. The current session id lives in `session.store`.
**Files:** `features/sessions/api/sessions.api.ts`, `features/sessions/store/session.store.ts`, `features/sessions/hooks/use-sessions.ts`, `features/sessions/components/session-list.tsx`

> **Contract caveat:** P3 (Task 6) binds `sessions.user_id` and enforces ownership, but the doc does **not** specify a `GET /api/sessions` list endpoint or a `GET /api/sessions/{id}/history` endpoint — P3 focuses on auth + isolation, not a session-listing API. This task is written against the **expected** P3-era shape and is validated by Zod; if the deployed backend names these differently, only `sessions.api.ts` changes. The schemas are defensive (`.passthrough()` tolerated). Until the backend ships listing, the MSW mock (Task 11) provides them so the flag-on path is fully exercisable.

```ts
// features/sessions/api/sessions.api.ts
import { z } from "zod";
import { request } from "@/lib/api/http-client";
import { MessageSchema } from "@/features/chat/api/chat.schemas"; // M1's Message Zod

export const SessionSummarySchema = z.object({
  id: z.string(),
  title: z.string().nullable().optional(),
  updated_at: z.string().optional(),
});
export type SessionSummary = z.infer<typeof SessionSummarySchema>;

const SessionListSchema = z.array(SessionSummarySchema);
const SessionHistorySchema = z.object({
  session_id: z.string(),
  messages: z.array(MessageSchema),
});

export const sessionsApi = {
  list: () =>
    request("/sessions", { method: "GET", schema: SessionListSchema, auth: true }),
  create: () =>
    request("/sessions", { method: "POST", schema: SessionSummarySchema, auth: true }),
  history: (id: string) =>
    request(`/sessions/${id}/history`, {
      method: "GET",
      schema: SessionHistorySchema,
      auth: true,
    }),
};
```

```ts
// features/sessions/store/session.store.ts
import { create } from "zustand";

interface SessionState {
  currentSessionId: string | null;
  setCurrentSessionId: (id: string | null) => void;
}

export const useSessionStore = create<SessionState>((set) => ({
  currentSessionId: null,
  setCurrentSessionId: (id) => set({ currentSessionId: id }),
}));
```

```ts
// features/sessions/hooks/use-sessions.ts
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { flags } from "@/lib/flags";
import { sessionsApi } from "@/features/sessions/api/sessions.api";
import { useSessionStore } from "@/features/sessions/store/session.store";
import { useChatStore } from "@/features/chat/store/chat.store"; // M1's chat store

export function useSessions() {
  const qc = useQueryClient();
  const setCurrentSessionId = useSessionStore((s) => s.setCurrentSessionId);
  const setMessages = useChatStore((s) => s.setMessages);

  const list = useQuery({
    queryKey: ["sessions"],
    queryFn: sessionsApi.list,
    enabled: flags.auth, // only fetch when auth is live
  });

  const create = useMutation({
    mutationFn: sessionsApi.create,
    onSuccess: (s) => {
      setCurrentSessionId(s.id);
      setMessages([]); // fresh chat
      qc.invalidateQueries({ queryKey: ["sessions"] });
    },
  });

  const resume = useMutation({
    mutationFn: (id: string) => sessionsApi.history(id),
    onSuccess: (data) => {
      setCurrentSessionId(data.session_id);
      setMessages(data.messages); // hydrate chat store with server history
    },
  });

  return { list, create, resume };
}
```

```tsx
// features/sessions/components/session-list.tsx
"use client";
import { Button } from "@/components/ui/button";
import { Plus } from "lucide-react";
import { useSessions } from "@/features/sessions/hooks/use-sessions";
import { useSessionStore } from "@/features/sessions/store/session.store";
import { cn } from "@/lib/utils";

export function SessionList() {
  const { list, create, resume } = useSessions();
  const current = useSessionStore((s) => s.currentSessionId);

  return (
    <div className="flex flex-col gap-1">
      <Button variant="ghost" className="justify-start" onClick={() => create.mutate()}>
        <Plus className="mr-2 h-4 w-4" /> New chat
      </Button>
      {list.isLoading && <p className="px-2 text-sm text-muted-foreground">Loading…</p>}
      {list.data?.map((s) => (
        <button
          key={s.id}
          onClick={() => resume.mutate(s.id)}
          className={cn(
            "truncate rounded px-2 py-1.5 text-left text-sm hover:bg-accent",
            current === s.id && "bg-accent font-medium",
          )}
        >
          {s.title ?? "Untitled chat"}
        </button>
      ))}
    </div>
  );
}
```

### Task 9 — `chat.api`: user-owned session id + Bearer (flag-gated)

**Goal:** when `flags.auth` is on, send the **server-owned** `currentSessionId` and let the interceptor attach `Bearer` (`auth: true`); when off, use today's anonymous client-generated id and no Bearer (`auth: false`). The `ChatRequest`/`ChatResponse` wire shape is unchanged (contract §2.4).
**Files:** `features/chat/api/chat.api.ts` (delta)

```ts
// features/chat/api/chat.api.ts (delta)
import { flags } from "@/lib/flags";
import { request } from "@/lib/api/http-client";
import { useSessionStore } from "@/features/sessions/store/session.store";
import { ChatResponseSchema, type ChatResponse } from "./chat.schemas";

// Anonymous fallback — preserves today's localStorage rag_session_id behavior.
function anonymousSessionId(): string {
  if (typeof window === "undefined") return "";
  let id = localStorage.getItem("rag_session_id");
  if (!id) {
    id = crypto.randomUUID();
    localStorage.setItem("rag_session_id", id);
  }
  return id;
}

function resolveSessionId(): string {
  if (flags.auth) {
    // server-owned id; fall back to "" so backend mints+binds one on first use
    return useSessionStore.getState().currentSessionId ?? "";
  }
  return anonymousSessionId();
}

export async function sendMessage(
  message: string,
  webSearchAllowed: boolean,
): Promise<ChatResponse> {
  const res = await request<ChatResponse>("/chat", {
    method: "POST",
    body: {
      message,
      session_id: resolveSessionId(),
      web_search_allowed: webSearchAllowed,
    },
    schema: ChatResponseSchema,
    auth: flags.auth, // Bearer attached only when auth is live
  });

  // Persist a server-minted session id into the right place per mode.
  if (res.session_id) {
    if (flags.auth) {
      useSessionStore.getState().setCurrentSessionId(res.session_id);
    } else if (typeof window !== "undefined") {
      localStorage.setItem("rag_session_id", res.session_id);
    }
  }
  return res;
}
```

> Apply the identical `auth: flags.auth` + `resolveSessionId()` pattern to `upload` and `cleanup` in their respective `*.api.ts` (multipart upload sends `session_id` as a form field; cleanup sends it in the JSON body). The interceptor handles the Bearer; you only pass `auth: flags.auth`.

### Task 10 — `user-menu` in the sidebar

**Goal:** avatar + email/username + logout, shown only when the flag is on and authenticated.
**Files:** `features/auth/components/user-menu.tsx`, `components/layout/app-sidebar.tsx` (delta)

```tsx
// features/auth/components/user-menu.tsx
"use client";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import { LogOut } from "lucide-react";
import { useAuth } from "@/features/auth/hooks/use-auth";

export function UserMenu() {
  const { user, email, isAuthenticated, logout } = useAuth();
  if (!isAuthenticated) return null;
  const label = user?.username ?? email ?? "Account";
  const initial = (user?.username ?? email ?? "?").charAt(0).toUpperCase();

  return (
    <div className="flex items-center gap-2 border-t border-border p-2">
      <Avatar className="h-7 w-7">
        <AvatarFallback>{initial}</AvatarFallback>
      </Avatar>
      <span className="flex-1 truncate text-sm">{label}</span>
      <Button variant="ghost" size="icon" aria-label="Log out" onClick={logout}>
        <LogOut className="h-4 w-4" />
      </Button>
    </div>
  );
}
```

```tsx
// components/layout/app-sidebar.tsx (delta) — mount session list + user menu when auth is on
import { flags } from "@/lib/flags";
import { SessionList } from "@/features/sessions/components/session-list";
import { UserMenu } from "@/features/auth/components/user-menu";
// ... inside the sidebar body:
{flags.auth && <SessionList />}
{/* existing "clear session" controls stay for the flag-off path */}
{flags.auth && <UserMenu />}
```

---

## 7. Feature-Flag Behavior Matrix

`NEXT_PUBLIC_FEATURE_AUTH` controls every auth surface. The OFF column **must equal today's behavior** (current-state §4).

| Surface | Flag **OFF** (default — today) | Flag **ON** |
|---|---|---|
| Routing to `/` (chat) | Open; no guard; `<AuthGuard>` is a passthrough | `<AuthGuard>` redirects unauthenticated → `/login?next=/` after hydration |
| `/login`, `/register` routes | Exist but unused on the happy path; nothing redirects there | Entry points; successful auth redirects to `next` or `/` |
| `session_id` source | Client-generated `uuid` in `localStorage` (`rag_session_id`) — anonymous | Server-owned, user-scoped; from `session.store.currentSessionId` (backend mints+binds on first use) |
| `Authorization` header | **Never attached** (`auth: flags.auth` ⇒ `false`) | `Bearer <access>` attached to `auth:true` requests by the interceptor |
| `401` handling | Surfaced as an ordinary HTTP error (today's `Backend error: 401`) | Single-flight refresh → retry once; on failure clear store + redirect `/login` |
| `403` handling | Ordinary HTTP error | Typed `ApiError{kind:"forbidden"}`; surfaced as "no access"; never retried |
| Sidebar | "Clear session" only (today) | `<SessionList>` (server sessions + resume + new) and `<UserMenu>` (avatar + logout); legacy clear-session may remain |
| `/chat` payload shape | `{message, session_id, web_search_allowed}` | **Same shape** — only the `session_id` source + Bearer header differ |
| TanStack `["sessions"]` query | `enabled:false` ⇒ never fires | `enabled:true` ⇒ fetches the user's sessions |
| Token store | Persisted but empty/unused; no reads on the request path | Holds access+refresh(+user/email); read by interceptor & guard |

**Proof that flag-off ≡ today:** with `flags.auth === false`, (a) the interceptor's `if (flags.auth && auth)` blocks never run → no Bearer, no refresh; (b) `resolveSessionId()` returns the anonymous `localStorage` id exactly as `services/api.ts:9-17` does today; (c) `AuthGuard` returns `<>{children}</>` unconditionally; (d) `SessionList`/`UserMenu` are not mounted (`{flags.auth && …}`); (e) the `["sessions"]` query is `enabled:false`. No auth code is on the hot path.

---

## 8. Testing & Verification

**MSW handlers (`test/msw/handlers.auth.ts`):**
- `POST /auth/register` → `201` `UserOut`; duplicate email → `409`.
- `POST /auth/login` → `200` `TokenPair`; bad creds → `401` (generic).
- `POST /auth/refresh` → `200` fresh `TokenPair` when given a known refresh token; an access token (or unknown) → `401`.
- `GET/POST /sessions`, `GET /sessions/:id/history` → user-scoped fixtures; a request without `Authorization` → `401`; a session owned by another user → `403`.
- A `/chat` handler that returns `401` **once** then `200` (to drive the refresh-retry test), and one that returns `403`.

**Unit tests:**
1. **Interceptor refresh-once (`http-client.refresh.test.ts`)** — (a) `401`→refresh→retry **succeeds**: stub `/chat` 401-then-200, assert `/auth/refresh` called exactly once and the final result resolves with the 200 body. (b) **refresh fails** → `authStore.clear()` called + redirect to `/login` + rejects `ApiError{kind:"unauthorized"}`. (c) **single-flight**: fire 3 concurrent `auth:true` requests that all 401; assert `/auth/refresh` is hit **once** and all 3 retry with the new token. (d) **no loop**: retry that 401s again does **not** trigger a second refresh (`__retried` guard) → clear+redirect. (e) **`403`** → `ApiError{kind:"forbidden"}`, no refresh attempted.
2. **`auth.store.test.ts`** — `setTokens` populates access/refresh; `clear` empties them; persisted JSON round-trips through `localStorage` under `rag_auth`; `onRehydrateStorage` flips `hasHydrated`; `partialize` excludes `hasHydrated`.
3. **`auth-guard.test.tsx`** — flag **off**: renders children, never redirects. Flag **on** + unauthenticated + hydrated: calls `router.replace("/login?next=…")` and renders nothing. Flag **on** + authenticated: renders children. Flag on + not-yet-hydrated: renders nothing, no redirect.
4. **`login-form.test.tsx`** — invalid email shows inline error and does not call the mutation; valid submit calls `useLogin().mutate`; a `401` shows the "Invalid email or password." toast. (Plus a `register-form` test for the `409` path.)

**Manual (flag-on against MSW or a P3 mock backend):** set `NEXT_PUBLIC_FEATURE_AUTH=true`; register → auto-login → land on `/`; send a chat message (verify `Bearer` in the network tab, `session_id` server-owned); create a second session, resume the first (history loads); let the access token expire (or force a `401`) and confirm a single silent refresh + retry; log out → redirected to `/login`, Query cache cleared.

**Manual (flag-off parity):** with the flag unset/`false`, repeat today's flow — send message, upload, clear session, theme toggle — and confirm **no** `Authorization` header, the anonymous `rag_session_id` is used, and `/login` is never forced. This is the M6 acceptance gate ("flag-off = today's anonymous flow").

**Gates:** `npm run lint`, `prettier --check`, `tsc --noEmit`, `vitest run` all green; flag-off Playwright core flow (M5) still passes unchanged.

---

## 9. Risks & Gotchas

1. **Tokens in `localStorage` → XSS exposure.** Any injected script can read `rag_auth`. **Mitigations:** strict CSP; we render markdown via `react-markdown` with HTML disabled (no `dangerouslyAllowHtml`); short access TTL (15 min, backend Task 1); refresh token cleared on logout; documented BFF/httpOnly-cookie upgrade path if the backend later supports cookie auth. Accepted because the contract is Bearer-from-body (Decision §3).
2. **Refresh stampede / infinite loop.** Solved by the module-level `refreshInFlight` single-flight promise (concurrent 401s share one refresh) and the `__retried` marker (a request triggers at most one refresh + one retry; a re-401 on the retry → clear+redirect, never a third attempt). Tested explicitly (§8.1c/d).
3. **SSR / hydration of the persisted store.** `persist` reads `localStorage`, which is client-only. The guard and any token read must wait for `hasHydrated` (set in `onRehydrateStorage`) before deciding to redirect — otherwise the first client render sees `accessToken === null` and bounces an authenticated user. `AuthGuard` returns `null` until hydrated. `<html suppressHydrationWarning>` (M0) already covers theme; the store never renders token values into markup, so no hydration-mismatch text.
4. **Route-group + middleware vs client guard (Next 16 App Router).** Edge middleware cannot read `localStorage`; a middleware redirect would split-brain against the client store. We deliberately use a **client** `AuthGuard` (Decision §3). The `(auth)` group has its own chrome-less layout so login/register never render the sidebar/guard.
5. **CORS / credentials.** P3 tightens CORS to an explicit `CORS_ALLOWED_ORIGINS` allow-list (backend Task 8). The frontend origin **must** be listed or every authenticated request fails preflight. We send `Authorization` (a non-simple header) → browsers preflight `OPTIONS`; the backend must allow it (`allow_headers=["*"]`, backend line 516). Document the required origin in `.env`/ops notes.
6. **Auth-endpoint path prefix.** `/auth/*` is a **sibling** of `/api`, but `NEXT_PUBLIC_API_URL` ends in `/api`. The naive `${base}/auth/login` yields `/api/auth/login` (wrong). `authUrl()` strips the trailing `/api` (Task 2). Verify against the real backend mount.
7. **Clock skew on expiry.** Don't pre-empt refresh with a client timer comparing `exp` to `Date.now()` — client clocks drift. Treat a real `401` as the only refresh trigger; the backend already tolerates `leeway=10` (backend Task 3 line 283).
8. **Logout must clear the Query cache.** Otherwise the next user (same browser) could see the previous user's cached `["sessions"]`/history. `useAuth().logout` calls `queryClient.clear()` before redirecting.
9. **Anonymous → authenticated session migration.** The old anonymous `rag_session_id` is **not** transferable — it has no owner and the backend would bind a brand-new server session on first authenticated `/chat`. We do **not** attempt to migrate anonymous history into a user account (no backend endpoint for it; out of scope). On first login the chat starts fresh / from the user's server sessions. Leave the stale `rag_session_id` key untouched so toggling the flag back off restores the anonymous session.
10. **`refresh` must never re-enter the interceptor.** `authApi.refresh` is called with `auth:false` + `absoluteUrl`, so it skips Bearer-attach and the 401-refresh branch; otherwise a 401 on `/auth/refresh` would recurse. Guaranteed by the `auth:false` flag and the single-flight gate.
11. **Sessions listing endpoint is assumed, not specified by P3.** `sessions.api.ts` targets an expected `/sessions` shape; Zod-validated and MSW-mocked. If the deployed backend differs, only that one file changes — the store/hooks/UI are insulated.

---

## 10. Exit Criteria (checkable)

1. **Flag-off parity.** With `NEXT_PUBLIC_FEATURE_AUTH` unset/`false`: no `Authorization` header on any request, anonymous `rag_session_id` used, `/login` never forced, sidebar shows today's controls. Playwright core flow (M5) passes unchanged. *(M6 acceptance gate.)*
2. **Flag-on register/login.** With the flag on (MSW or P3 backend): register → auto-login → tokens persisted in `rag_auth`; login of an existing user works; bad creds show the generic error.
3. **Bearer attached.** Every `auth:true` request carries `Authorization: Bearer <access>` when the flag is on (asserted in tests + visible in the network tab).
4. **Refresh-once-and-retry.** A `401` triggers exactly one `/auth/refresh`, one retry; concurrent 401s share one refresh (single-flight); a failed refresh clears the store and redirects to `/login`; no infinite loop. *(Unit-tested.)*
5. **403 surfaced, not retried.** A cross-user `403` yields `ApiError{kind:"forbidden"}` and is shown as a forbidden error; no refresh is attempted.
6. **Server-owned sessions + resume.** With the flag on, the sidebar lists the user's sessions; "new chat" creates one; selecting a session loads its history into the chat store; the active session is highlighted.
7. **Guarded chat route.** Flag on + unauthenticated → redirected to `/login`; flag off → open. No flash of protected content (guard renders `null` pre-hydration / unauthenticated).
8. **Logout.** Clears the auth store **and** the Query cache, then redirects to `/login`.
9. **All gates green.** `lint`, `prettier --check`, `tsc --noEmit`, `vitest run` pass; new unit/component tests included.

---

## 11. Commit Plan

Conventional commits, each leaving the tree releasable and the flag-off path untouched:

1. `feat(auth): Zod auth contracts + auth.api (register/login/refresh) matching P3` — Tasks 1–2.
2. `feat(auth): persisted Zustand token store (hydration-safe)` — Task 3.
3. `feat(api): activate http-client auth interceptor — Bearer + single-flight 401→refresh→retry + typed 403` — Task 4 (+ `api-error` `kind`).
4. `feat(auth): use-login/use-register/use-auth mutations + redirect` — Task 5.
5. `feat(auth): (auth)/login + (auth)/register routes and forms` — Task 6.
6. `feat(auth): flag-gated AuthGuard protecting the chat route` — Task 7.
7. `feat(sessions): server-owned session list, store, resume + new-session` — Task 8.
8. `feat(chat): user-owned session id + Bearer on chat/upload/cleanup (flag-gated)` — Task 9.
9. `feat(auth): user-menu (avatar + logout) in sidebar` — Task 10.
10. `test(auth): MSW handlers + interceptor single-flight/refresh, store, guard, forms` — Task 11/§8.
11. `docs(auth): record NEXT_PUBLIC_FEATURE_AUTH + auth-endpoint origin in .env.example/README` — flag + ops notes.

> Ship behind `NEXT_PUBLIC_FEATURE_AUTH=false` by default; enable in an environment only after a P3 backend is reachable and its origin is in `CORS_ALLOWED_ORIGINS`.
