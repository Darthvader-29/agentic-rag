"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { m } from "framer-motion";
import { Dialog as DialogPrimitive } from "radix-ui";
import { useChat, resetSession } from "@/features/chat/hooks/use-chat";
import { useChatStore, createMessage } from "@/features/chat/store/chat.store";
import { getSessionId } from "@/features/chat/api/chat.api";
import { isNearBottom } from "@/features/chat/lib/scroll";
import { env } from "@/lib/env";
import { flags } from "@/lib/flags";
import { spring } from "@/lib/motion";
import { cn } from "@/lib/utils";
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
  // Whether the user is "stuck" to the bottom (follow the stream) vs scrolled up to read. Starts
  // true so the first turn scrolls into view.
  const stickToBottomRef = useRef(true);
  const reduced = useReducedMotion();

  // a11y (WCAG 2.4.3): the sidebar collapse/expand toggles each REMOVE the control that was just
  // activated (the in-sidebar "Toggle sidebar" button when collapsing; the floating "Open sidebar"
  // button when expanding). Without intervention focus drops to <body>. We move focus to the
  // counterpart that becomes visible after the toggle. The insights drawer is a radix Dialog, so
  // its own focus trap + restore-to-trigger is handled by radix.
  const openSidebarBtnRef = useRef<HTMLButtonElement>(null);
  // What to focus after the NEXT sidebar state change (consumed by the effect below).
  const pendingSidebarFocus = useRef<"open-button" | "sidebar-toggle" | null>(
    null
  );

  // The session the panels fetch against. Empty string on the server (SSR-safe); the panels'
  // own query gating + empty-state handle an empty id gracefully.
  const sessionId = getSessionId();

  const collapseSidebar = useCallback(() => {
    pendingSidebarFocus.current = "open-button";
    setIsSidebarOpen(false);
  }, []);
  const expandSidebar = useCallback(() => {
    pendingSidebarFocus.current = "sidebar-toggle";
    setIsSidebarOpen(true);
  }, []);

  // Move focus to the control that replaced the one the user just activated.
  useEffect(() => {
    const target = pendingSidebarFocus.current;
    if (!target) return;
    pendingSidebarFocus.current = null;
    if (target === "open-button") {
      openSidebarBtnRef.current?.focus();
    } else {
      // The in-sidebar collapse toggle re-appears when the sidebar expands.
      const el = document.querySelector<HTMLElement>(
        '[aria-label="Toggle sidebar"]'
      );
      el?.focus();
    }
  }, [isSidebarOpen]);

  // Track whether the user is near the bottom of the scroll viewport. While they've scrolled up
  // we must NOT yank them back down on every streamed token (B24).
  useEffect(() => {
    const viewport = scrollRef.current?.closest(
      "[data-radix-scroll-area-viewport]"
    ) as HTMLElement | null;
    if (!viewport) return;
    const onScroll = () => {
      stickToBottomRef.current = isNearBottom(viewport);
    };
    viewport.addEventListener("scroll", onScroll, { passive: true });
    return () => viewport.removeEventListener("scroll", onScroll);
  }, []);

  // Auto-scroll to the latest content — but ONLY when the user is already at the bottom, so a
  // streamed token doesn't hijack the view while they're reading earlier messages.
  useEffect(() => {
    if (stickToBottomRef.current) {
      scrollRef.current?.scrollIntoView({ behavior: "smooth" });
    }
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
          onToggle={collapseSidebar}
        />
      </m.div>

      <div className="border-border bg-background relative my-0 mr-0 flex h-full flex-1 flex-col overflow-hidden rounded-l-2xl border-l shadow-xl dark:shadow-none">
        {!isSidebarOpen && (
          <div className="absolute top-4 left-4 z-10">
            <Button
              ref={openSidebarBtnRef}
              variant="ghost"
              size="icon"
              onClick={expandSidebar}
              aria-label="Open sidebar"
              className="hover:bg-accent"
            >
              <Menu className="text-muted-foreground h-5 w-5" />
            </Button>
          </div>
        )}

        {/* Phase 7: Insights drawer — a focus-trapped radix Dialog (sheet). The trigger stays
            mounted while the feature is enabled so radix can restore focus to it on close/Escape
            (WCAG 2.4.3). Gated on INSIGHTS_ENABLED, so with both flags off neither the trigger nor
            the lazy panel chunks are ever evaluated. */}
        {INSIGHTS_ENABLED && (
          <DialogPrimitive.Root
            open={isInsightsOpen}
            onOpenChange={setIsInsightsOpen}
          >
            <div className="absolute top-4 right-4 z-10">
              <DialogPrimitive.Trigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label="Open insights"
                  className="hover:bg-accent"
                >
                  <Sparkles className="text-muted-foreground h-5 w-5" />
                </Button>
              </DialogPrimitive.Trigger>
            </div>

            <DialogPrimitive.Portal>
              <DialogPrimitive.Overlay
                className={cn(
                  "data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:animate-in data-[state=open]:fade-in-0 fixed inset-0 z-50 bg-black/50"
                )}
              />
              {/* Right-hand sheet: radix Content gives the focus trap, Escape-to-close, and
                  focus-restore-to-trigger for free, fixing the prior "toggles drop focus" gap. */}
              <DialogPrimitive.Content
                aria-label="Insights"
                className={cn(
                  "bg-background border-border data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:slide-in-from-right-2 data-[state=closed]:slide-out-to-right-2 fixed top-0 right-0 z-50 flex h-full w-[340px] max-w-[90vw] flex-col border-l shadow-xl duration-200 outline-none"
                )}
              >
                <div className="border-border flex items-center justify-between border-b px-4 py-3">
                  <DialogPrimitive.Title className="text-foreground flex items-center gap-2 text-sm font-semibold">
                    <Sparkles className="h-4 w-4" aria-hidden="true" />
                    Insights
                  </DialogPrimitive.Title>
                  <DialogPrimitive.Description className="sr-only">
                    Knowledge graph and conversation memory for this session.
                  </DialogPrimitive.Description>
                  <DialogPrimitive.Close asChild>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-7 w-7"
                      aria-label="Close insights"
                    >
                      <PanelRightClose className="text-muted-foreground h-4 w-4" />
                    </Button>
                  </DialogPrimitive.Close>
                </div>

                <ScrollArea className="flex-1">
                  <div className="space-y-4 p-4">
                    {flags.knowledgeGraph && (
                      <GraphPanel sessionId={sessionId} />
                    )}
                    {flags.memory && <MemoryPanel sessionId={sessionId} />}
                  </div>
                </ScrollArea>
              </DialogPrimitive.Content>
            </DialogPrimitive.Portal>
          </DialogPrimitive.Root>
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
    </div>
  );
}
