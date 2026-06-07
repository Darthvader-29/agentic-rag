"use client";

import { useEffect, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { m } from "framer-motion";
import { useChat, resetSession } from "@/features/chat/hooks/use-chat";
import { useChatStore, createMessage } from "@/features/chat/store/chat.store";
import { getSessionId } from "@/features/chat/api/chat.api";
import { env } from "@/lib/env";
import { flags } from "@/lib/flags";
import { spring } from "@/lib/motion";
import { useReducedMotion } from "@/hooks/use-reduced-motion";

import { Sidebar } from "@/components/chat/sidebar";
import { ChatInput } from "@/components/chat/chat-input";
import { EmptyState } from "@/components/chat/empty-state";
import { MessageList } from "@/features/chat/components/message-list";
import { FreeTierBanner } from "@/features/keys/components/free-tier-banner";
import { FreeTierExhaustedDialog } from "@/features/keys/components/free-tier-exhausted-dialog";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Button } from "@/components/ui/button";
import { Menu, PanelRightClose, Sparkles } from "lucide-react";
import { toast } from "sonner";

const sidebarVariants = {
  open: { width: 256, opacity: 1 },
  closed: { width: 0, opacity: 0 },
};

// Right-hand "Insights" drawer width animation (mirrors the left sidebar spring).
const insightsVariants = {
  open: { width: 340, opacity: 1 },
  closed: { width: 0, opacity: 0 },
};

// Phase 7 mount points — lazy + flag-gated. The dynamic imports are only ever evaluated when
// their flag is on (the gate in the JSX short-circuits), so the chunks stay unfetched while the
// features ship dark. Each panel is OWNED by its feature lane and must degrade to an empty state
// when its backend endpoint 404s/errors.
//   - GraphPanel uses react-force-graph (canvas + window) ⇒ ssr:false is REQUIRED.
//   - MemoryPanel renders client-only data (localStorage session id + relative timestamps), so
//     SSR'ing it would mismatch the client on hydration ⇒ ssr:false.
const GraphPanel = dynamic(
  () => import("@/features/knowledge-graph/components/graph-panel"),
  { ssr: false }
);
const MemoryPanel = dynamic(
  () => import("@/features/memory/components/memory-panel"),
  { ssr: false }
);

// Whether ANY insights surface is enabled — gates the whole drawer + its toggle.
const INSIGHTS_ENABLED = flags.knowledgeGraph || flags.memory;

export function ChatScreen() {
  const { messages, sendMessage } = useChat();
  const isLoading = useChatStore((s) => s.isLoading);
  const addMessage = useChatStore((s) => s.addMessage);
  const [isSidebarOpen, setIsSidebarOpen] = useState(true);
  // Phase 7 Insights drawer — closed by default; only ever opened when a flag enables a panel.
  const [isInsightsOpen, setIsInsightsOpen] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const reduced = useReducedMotion();

  // The session the panels fetch against. Empty string on the server (SSR-safe); the panels'
  // own query gating + empty-state handle an empty id gracefully.
  const sessionId = getSessionId();

  // Auto-scroll (ported from page.tsx:29-33).
  useEffect(() => {
    scrollRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isLoading]);

  // Cleanup beacon on tab close (ported from page.tsx:36-54).
  useEffect(() => {
    const handleBeforeUnload = () => {
      const sessionId = getSessionId();
      if (!sessionId) return;
      const payload = JSON.stringify({ session_id: sessionId, file_keys: [] });
      navigator.sendBeacon(
        `${env.NEXT_PUBLIC_API_URL}/cleanup`,
        new Blob([payload], { type: "application/json" })
      );
    };
    window.addEventListener("beforeunload", handleBeforeUnload);
    return () => window.removeEventListener("beforeunload", handleBeforeUnload);
  }, []);

  const handleClearSession = async () => {
    await resetSession();
    toast.success("Chat history cleared");
  };

  return (
    <div className="bg-background flex h-screen w-full overflow-hidden">
      {/* Spring-driven sidebar — replaces CSS width/opacity transition. */}
      <m.div
        initial={false}
        animate={isSidebarOpen ? "open" : "closed"}
        variants={sidebarVariants}
        transition={reduced ? { duration: 0 } : spring}
        className="overflow-hidden"
      >
        <Sidebar
          onClearSession={handleClearSession}
          onToggle={() => setIsSidebarOpen(false)}
        />
      </m.div>

      <div className="border-border bg-background relative my-0 mr-0 flex h-full flex-1 flex-col overflow-hidden rounded-l-2xl border-l shadow-xl dark:shadow-none">
        {!isSidebarOpen && (
          <div className="absolute top-4 left-4 z-10">
            <Button
              variant="ghost"
              size="icon"
              onClick={() => setIsSidebarOpen(true)}
              aria-label="Open sidebar"
              className="hover:bg-accent"
            >
              <Menu className="text-muted-foreground h-5 w-5" />
            </Button>
          </div>
        )}

        {/* Phase 7: Insights drawer toggle — only shown when a panel flag is on. */}
        {INSIGHTS_ENABLED && !isInsightsOpen && (
          <div className="absolute top-4 right-4 z-10">
            <Button
              variant="ghost"
              size="icon"
              onClick={() => setIsInsightsOpen(true)}
              aria-label="Open insights"
              className="hover:bg-accent"
            >
              <Sparkles className="text-muted-foreground h-5 w-5" />
            </Button>
          </div>
        )}

        {/* Free-tier disclaimer — flag-gated; visible only to keyless users (M7). */}
        <FreeTierBanner />

        {/* BYOK upsell — opens when a turn fails with free_tier_exhausted (M7). Portals. */}
        <FreeTierExhaustedDialog />

        <ScrollArea className="max-h-[calc(100vh-80px)] flex-1 p-4">
          <div className="mx-auto max-w-4xl space-y-6 pt-10 pb-10">
            {messages.length === 0 ? (
              <div className="mt-10">
                <EmptyState />
              </div>
            ) : (
              <MessageList messages={messages} isLoading={isLoading} />
            )}
            <div ref={scrollRef} />
          </div>
        </ScrollArea>

        <ChatInput
          isLoading={isLoading}
          onSend={sendMessage}
          onFileUploaded={(fileName) => {
            addMessage(
              createMessage({
                role: "assistant",
                content: `📄 "${fileName}" uploaded and queued for ingestion.`,
                status: "done",
              })
            );
          }}
        />
      </div>

      {/* Phase 7 Insights drawer — right-hand panel hosting the knowledge-graph + memory panels.
          The whole drawer is gated on INSIGHTS_ENABLED, so when both flags are off nothing here
          (including the lazy imports) is ever evaluated. The spring-driven width mirrors the left
          sidebar. Each panel is flag-gated INDIVIDUALLY and owns its own loading/empty/404 state. */}
      {INSIGHTS_ENABLED && (
        <m.div
          initial={false}
          animate={isInsightsOpen ? "open" : "closed"}
          variants={insightsVariants}
          transition={reduced ? { duration: 0 } : spring}
          className="border-border bg-background h-full overflow-hidden border-l"
        >
          <div className="flex h-full w-[340px] flex-col">
            <div className="border-border flex items-center justify-between border-b px-4 py-3">
              <span className="text-foreground flex items-center gap-2 text-sm font-semibold">
                <Sparkles className="h-4 w-4" />
                Insights
              </span>
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7"
                onClick={() => setIsInsightsOpen(false)}
                aria-label="Close insights"
              >
                <PanelRightClose className="text-muted-foreground h-4 w-4" />
              </Button>
            </div>

            <ScrollArea className="flex-1">
              <div className="space-y-4 p-4">
                {flags.knowledgeGraph && <GraphPanel sessionId={sessionId} />}
                {flags.memory && <MemoryPanel sessionId={sessionId} />}
              </div>
            </ScrollArea>
          </div>
        </m.div>
      )}
    </div>
  );
}
