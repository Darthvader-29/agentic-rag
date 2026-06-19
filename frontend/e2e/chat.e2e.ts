import { test, expect, type Page } from "@playwright/test";

// R21 / M5 — the core chat flow against a stubbed backend.
//
// The backend is mocked at the network layer with Playwright's `page.route()` (the in-scope
// equivalent of the M5 "MSW or stubbed backend" plan — no app changes, no service worker to
// register). Streaming is OFF in playwright.config.ts, so the app uses the blocking `POST /api/chat`
// path: one JSON request → one assistant turn. The flow exercised is:
//   load (empty state) → send → assistant reply + route badge → upload → theme toggle → reset.

const ANSWER_PREFIX = "Echo:";

/**
 * Install deterministic stubs for the three backend endpoints the blocking chat flow touches.
 * `**​/api/...` matches whatever origin NEXT_PUBLIC_API_URL is pinned to (here localhost:3000/api).
 */
async function stubBackend(page: Page): Promise<void> {
  // Blocking chat: echo the user's message back so the assertion is tied to the input.
  await page.route("**/api/chat", async (route) => {
    const body = route.request().postDataJSON() as { message?: string };
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        answer: `${ANSWER_PREFIX} ${body?.message ?? ""}`.trim(),
        route: "RAG", // valid flat enum → renders the RAG route badge
        context_count: 2,
        session_id: "e2e-session",
      }),
    });
  });

  // Multipart upload (presigned flag off → legacy path). The hook only needs a 2xx JSON body.
  await page.route("**/api/upload", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ status: "uploaded", s3_key: "e2e/uploads/mock" }),
    })
  );

  // Session cleanup (fired by Reset Session and the unload beacon).
  await page.route("**/api/cleanup", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ status: "ok" }),
    })
  );
}

test.describe("core chat flow (stubbed backend)", () => {
  test.beforeEach(async ({ page }) => {
    await stubBackend(page);
    await page.goto("/");
    // Empty state on load: the RAG Assistant heading is visible before any turn.
    await expect(
      page.getByRole("heading", { name: /rag assistant/i })
    ).toBeVisible();
  });

  test("send → assistant renders → upload → theme toggle → reset", async ({
    page,
  }) => {
    const question = "What is retrieval-augmented generation?";

    // 1) Send a message via the composer textarea (Enter submits; Shift+Enter newlines).
    const input = page.getByRole("textbox", { name: /message/i });
    await input.fill(question);
    await input.press("Enter");

    // The user bubble appears immediately. `exact` so it doesn't also match the assistant
    // echo ("Echo: <question>" contains the question as a substring → would be 2 matches).
    await expect(page.getByText(question, { exact: true })).toBeVisible();

    // 2) The assistant reply (stub echoes) + the route badge render. `exact` on the badge
    // excludes the "RAG Assistant" heading/author label (those aren't exactly "RAG").
    await expect(page.getByText(`${ANSWER_PREFIX} ${question}`)).toBeVisible();
    await expect(page.getByText("RAG", { exact: true }).first()).toBeVisible();

    // 3) Upload a document (legacy multipart path injects a synthetic confirmation message).
    await page.locator('input[type="file"]').setInputFiles({
      name: "notes.txt",
      mimeType: "text/plain",
      buffer: Buffer.from("hello from an e2e test document"),
    });
    // The legacy path posts a synthetic confirmation message into the chat. Match the in-chat
    // ingestion line specifically — a transient toast ("notes.txt uploaded") ALSO contains the
    // filename, so a bare /notes\.txt/ matches 2 elements and trips Playwright strict mode.
    await expect(
      page.getByText(/notes\.txt.*queued for ingestion/i)
    ).toBeVisible();

    // 4) Theme toggle: pick Dark, assert html.dark is set; then Light, assert it clears.
    const html = page.locator("html");
    const themeButton = page.getByRole("button", { name: /toggle theme/i });

    await themeButton.click();
    await page.getByRole("menuitem", { name: /^dark$/i }).click();
    await expect(html).toHaveClass(/dark/);

    await themeButton.click();
    await page.getByRole("menuitem", { name: /^light$/i }).click();
    await expect(html).not.toHaveClass(/dark/);

    // 5) Reset session: messages clear back to the empty state.
    await page.getByRole("button", { name: /reset session/i }).click();
    await expect(page.getByText(`${ANSWER_PREFIX} ${question}`)).toHaveCount(0);
    await expect(
      page.getByRole("heading", { name: /rag assistant/i })
    ).toBeVisible();
  });
});
