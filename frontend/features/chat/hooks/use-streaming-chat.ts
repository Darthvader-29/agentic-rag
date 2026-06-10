"use client";

import { useCallback, useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { useChatStore, errorToTurn } from "@/features/chat/store/chat.store";
import { getSessionId } from "@/features/chat/api/chat.api";
import { invalidateSessionMemory } from "@/features/memory/hooks/use-session-memory";
import { streamChat } from "@/lib/sse/stream-chat";
import { getChatModelSelection } from "@/features/keys/store/provider.store";
import { flags } from "@/lib/flags";
import { traceIdFromTraceparent } from "@/lib/observability/trace";
import {
  SSE_LAYERS,
  type SseRoute,
  type SseComponent,
  type SseDoneLayer,
} from "@/features/chat/api/chat.schemas";
import type { RouteType, Source, RetrievalLayer, MessageStats } from "@/types";

/**
 * Map the backend `done.route` to the frontend RouteType union so a streamed Message
 * is shape-identical to a blocking one. Authoritative form is the FLAT enum from 09
 * Appendix A (`RAG | WEB | BOTH | DIRECT`): `BOTH`→"WEB+RAG"; `RAG`/`WEB`/`DIRECT`
 * pass through. The legacy 07 `{destination, relevant}` object is tolerated defensively
 * (reconciled in M9).
 */
function mapRoute(route: SseRoute | null): RouteType {
  if (!route) return "DIRECT";
  // Legacy object form (07): map by destination.
  if (typeof route === "object") {
    return route.destination === "web_search" ? "WEB" : "RAG";
  }
  // Flat enum (09, authoritative).
  if (route === "BOTH") return "WEB+RAG";
  return route; // "RAG" | "WEB" | "DIRECT" are valid RouteType members
}

/**
 * The `citation` component is the SOURCES / provenance channel (09 §5):
 *   { type: "citation", items: [{ label, source_id, snippet }] }
 * Flatten the items of every collected citation component into the Message `sources`
 * shape the sources panel renders. Read tolerantly — the component schema validates only
 * the catalog `type` (M9), so each item is `unknown` until M10's strict per-type schema.
 * Malformed/absent items contribute nothing rather than throwing.
 */
/** Narrow an unknown value to a RetrievalLayer (Phase 7), or undefined. Tolerant by design. */
function asLayer(v: unknown): RetrievalLayer | undefined {
  return typeof v === "string" && (SSE_LAYERS as readonly string[]).includes(v)
    ? (v as RetrievalLayer)
    : undefined;
}

function citationsToSources(citations: SseComponent[]): Source[] {
  const sources: Source[] = [];
  for (const c of citations) {
    const items = (c as { items?: unknown }).items;
    if (!Array.isArray(items)) continue;
    for (const raw of items) {
      if (typeof raw !== "object" || raw === null) continue;
      const item = raw as {
        label?: unknown;
        source_id?: unknown;
        snippet?: unknown;
        url?: unknown;
        layer?: unknown;
      };
      const title =
        typeof item.label === "string" && item.label.length > 0
          ? item.label
          : typeof item.source_id === "string"
            ? item.source_id
            : "Source";
      sources.push({
        id:
          typeof item.source_id === "string"
            ? item.source_id
            : `citation-${sources.length}`,
        title,
        snippet: typeof item.snippet === "string" ? item.snippet : undefined,
        url: typeof item.url === "string" ? item.url : undefined,
        // Phase 7: optional retrieval-layer provenance (citation item carries it).
        layer: asLayer(item.layer),
      });
    }
  }
  return sources;
}

/**
 * Phase 7: fold the `done.layers` set onto citation-derived Sources. `done.layers` is the
 * de-duplicated SET of retrieval layers that fed the answer (answer-level, NOT per-source), so we
 * can only attribute a per-source layer unambiguously when EXACTLY ONE layer contributed — then
 * every source lacking an explicit citation-item layer inherits it. With several contributing
 * layers we can't attribute per-source from a set, so the per-citation `layer` stays authoritative
 * and we leave sources untouched (no guessing).
 */
function applyDoneLayers(
  sources: Source[],
  layers: SseDoneLayer[] | undefined
): Source[] {
  if (!layers || layers.length !== 1) return sources;
  const only = asLayer(layers[0]);
  if (!only) return sources;
  return sources.map((s) => (s.layer ? s : { ...s, layer: only }));
}

export function useStreamingChat() {
  const beginTurn = useChatStore((s) => s.beginTurn);
  const appendContent = useChatStore((s) => s.appendContent);
  const pushStep = useChatStore((s) => s.pushStep);
  const completeSteps = useChatStore((s) => s.completeSteps);
  const addComponent = useChatStore((s) => s.addComponent);
  const setSources = useChatStore((s) => s.setSources);
  const setRoute = useChatStore((s) => s.setRoute);
  const setStats = useChatStore((s) => s.setStats);
  const finalize = useChatStore((s) => s.finalize);
  const setStreaming = useChatStore((s) => s.setStreaming);
  const isStreaming = useChatStore((s) => s.isStreaming);
  const queryClient = useQueryClient();

  // One in-flight stream at a time; the controller powers the Stop button.
  const abortRef = useRef<AbortController | null>(null);

  // Abort any in-flight stream when the hook unmounts (e.g. navigating away mid-stream) so the
  // fetch doesn't dangle; the turn is then finalized by sendMessage's post-stream safety net.
  useEffect(() => () => abortRef.current?.abort(), []);

  const sendMessage = useCallback(
    async (text: string, webSearchAllowed: boolean) => {
      // The composer isn't disabled mid-stream, so a second send can arrive while one is still
      // in flight. Abort the previous stream first so its controller can't be orphaned — its own
      // turn is finalized by that call's post-stream safety net below.
      abortRef.current?.abort();

      // Optimistic two-message bootstrap (user "done" + empty assistant "streaming"), shared
      // with the blocking strategy so the two builders can't drift. Returns the assistant id
      // we stream INTO.
      const assistantId = beginTurn(text, webSearchAllowed);

      const controller = new AbortController();
      abortRef.current = controller;
      setStreaming(true);

      // Set by onDone/onError; the post-stream safety net finalizes the turn iff neither ran.
      let finalized = false;

      // The `citation` components are the SOURCES channel (09 §5). Collect them across
      // the stream and flush to the sources panel on `done` — no status-derived guess.
      const citations: SseComponent[] = [];

      // Phase 7 (FE-4): per-turn observability stats. Active ONLY when the flag is on; otherwise
      // `stats` stays null and nothing is ever written (no behavioural change). Timings use
      // performance.now() (monotonic), captured at request start and on each status stage.
      const observe = flags.observability;
      let stats: MessageStats | null = observe
        ? { startedAtMs: performance.now(), stages: [] }
        : null;
      if (stats) setStats(assistantId, stats);

      await streamChat(
        {
          message: text,
          session_id: getSessionId(),
          web_search_allowed: webSearchAllowed,
          // M7: optional per-conversation provider/model. Spreads to nothing when no
          // provider is selected ⇒ the backend default (free Gemini tier).
          ...getChatModelSelection(),
        },
        {
          signal: controller.signal,
          // Phase 7: stamp the trace id we sent (only fired when observability is on).
          onTrace: (traceparent) => {
            if (!stats) return;
            stats = { ...stats, traceId: traceIdFromTraceparent(traceparent) };
            setStats(assistantId, stats);
          },
          onStatus: (stage) => {
            // status stage → a live thinking step (feeds ThinkingSteps panel)
            pushStep(assistantId, { label: stage, state: "active" });
            // Phase 7: record the stage arrival offset for the stats panel.
            if (stats) {
              stats = {
                ...stats,
                stages: [
                  ...stats.stages,
                  { stage, atMs: performance.now() - stats.startedAtMs },
                ],
              };
              setStats(assistantId, stats);
            }
          },
          onToken: (chunk) => {
            // token chunk → append to the streaming body (+ M4 caret rides this)
            appendContent(assistantId, chunk);
          },
          onComponent: (component) => {
            // Store every whole rich block on message.components (M9 sink; M10 renders).
            addComponent(assistantId, component);
            // A citation block is provenance → collect it for the sources panel.
            if (component.type === "citation") citations.push(component);
          },
          onDone: ({ answer, route, layers: doneLayers }) => {
            // Canonical final body is done.answer (== concatenated tokens).
            const mapped = mapRoute(route);
            setRoute(assistantId, mapped);
            // Flip the live status steps to complete so the "Thinking…" spinner stops (the steps
            // were pushed as "active" and nothing else clears them) — matches the blocking hook.
            completeSteps(assistantId);
            // Sources come ONLY from citation components; none → leave [] (no fabricated count).
            // Phase 7: fold the done-event contributing-layer set onto the citation-derived sources.
            const sources = applyDoneLayers(
              citationsToSources(citations),
              doneLayers
            );
            if (sources.length > 0) setSources(assistantId, sources);
            // Phase 7: finalize the stats — totalMs, resolved route, tokens (null until the
            // backend reports them on done), trace id already set via onTrace.
            const patch: Partial<{ content: string; stats: MessageStats }> = {};
            // Only overwrite the body with done.answer when it's non-empty — an empty/missing
            // done.answer must NOT wipe the content already accumulated from token events (B26).
            if (answer) patch.content = answer;
            if (stats) {
              stats = {
                ...stats,
                totalMs: performance.now() - stats.startedAtMs,
                route: mapped,
                tokens: null,
              };
              patch.stats = stats;
            }
            finalize(assistantId, patch);
            finalized = true;
            // A finalized turn means the backend rewrote this session's memory — refresh the panel.
            invalidateSessionMemory(queryClient, getSessionId());
          },
          onError: (error) => {
            // Stop any prior stage's spinner, then record the error step.
            completeSteps(assistantId);
            pushStep(assistantId, { label: "error", state: "error" });
            setRoute(assistantId, "ERROR");
            // Shared error→turn recipe (branches on the machine-readable CODE, docs/09 §3, not
            // the HTTP status). A free-tier-exhausted code (from either delivery path) is captured
            // on the message so M7's BYOK "add your own key" CTA can key off `errorCode`. This hook
            // keeps its own store-writes (setRoute + finalize); only the derivation is shared.
            const { content, errorCode } = errorToTurn(error);
            finalize(assistantId, { errorCode, content });
            finalized = true;
          },
        }
      );

      // Safety net: if the stream ended without finalizing the turn — Stop pressed mid-stream, a
      // malformed `done` the lib/sse layer dropped, or a body that closed without a terminal event
      // — the message would otherwise hang in "streaming" forever. Finalize it with what streamed.
      if (!finalized) finalize(assistantId);

      // Only clear the shared stream state if WE are still the active stream: a newer sendMessage
      // may have replaced abortRef while we awaited (see the abort-previous guard above).
      if (abortRef.current === controller) {
        abortRef.current = null;
        setStreaming(false);
      }
    },
    [
      beginTurn,
      appendContent,
      pushStep,
      completeSteps,
      addComponent,
      setSources,
      setRoute,
      setStats,
      finalize,
      setStreaming,
      queryClient,
    ]
  );

  const stop = useCallback(() => {
    abortRef.current?.abort(); // AbortError → streamChat resolves cleanly
    abortRef.current = null;
    setStreaming(false);
  }, [setStreaming]);

  return { sendMessage, stop, isStreaming };
}
