"use client";

import * as React from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import dynamic from "next/dynamic";
import { m } from "framer-motion";
import { Bot, User } from "lucide-react";

import { Message } from "@/types";
import { cn } from "@/lib/utils";
import { flags } from "@/lib/flags";
import { markdownComponents } from "@/lib/markdown/components";
import { messageVariants, reduceVariants, layoutSpring } from "@/lib/motion";
import { useReducedMotion } from "@/hooks/use-reduced-motion";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { RouteBadge } from "@/features/chat/components/route-badge";
import { ThinkingSteps } from "@/features/chat/components/thinking-steps";
import { SourcesPanel } from "@/features/chat/components/sources-panel";
import { MessageActions } from "@/features/chat/components/message-actions";
import { StreamingCaret } from "@/features/chat/components/streaming-caret";
import { ComponentBlock } from "@/features/chat/components/rich/component-block";
import { normalizeComponents } from "@/features/chat/components/rich/component.schemas";

// Phase 7 (FE-4): the per-turn observability stats panel is lazy-loaded and flag-gated. The
// dynamic import means the chunk is never fetched when observability is off (the gate below
// short-circuits before this renders). The feature lane creates stats-panel.tsx; until then the
// flag stays OFF so this import is inert. ssr:false isn't needed (no canvas/window access), but
// the component is purely client UI.
const StatsPanel = dynamic(
  () => import("@/features/chat/components/stats-panel"),
  { ssr: false }
);

interface ChatMessageProps {
  message: Message;
}

function ChatMessageImpl({ message }: ChatMessageProps) {
  const isUser = message.role === "user";
  const reduced = useReducedMotion();
  const isStreaming = message.status === "streaming";

  // M10: a P6 `citation` block is the precise provenance channel. If one is present, suppress the
  // generic synthesized sources panel for this message so provenance isn't shown twice (R7).
  // normalizeComponents drops invalid blocks (defense-in-depth over the backend's own drop, §2.5).
  const hasCitation = normalizeComponents(message.components).some(
    (c) => c.type === "citation"
  );

  return (
    <m.div
      // Exclude the streaming message from layout projection — its height changes every
      // token and animating that reflow is pure jank. Settled messages get layout.
      layout={isStreaming ? false : "position"}
      transition={{ layout: layoutSpring }}
      variants={reduceVariants(messageVariants, reduced)}
      initial="initial"
      animate="animate"
      exit="exit"
      className={cn(
        "group flex w-full gap-4 rounded-xl p-5",
        isUser
          ? "bg-primary/5 flex-row-reverse"
          : "border-border bg-card border shadow-sm"
      )}
    >
      <Avatar className="border-border h-8 w-8 shrink-0 border">
        <AvatarFallback
          className={cn(
            isUser
              ? "bg-primary text-primary-foreground"
              : "bg-muted text-muted-foreground"
          )}
        >
          {isUser ? <User className="h-4 w-4" /> : <Bot className="h-4 w-4" />}
        </AvatarFallback>
      </Avatar>

      <div
        className={cn(
          "min-w-0 flex-1 space-y-2",
          isUser ? "text-right" : "text-left"
        )}
      >
        <div
          className={cn(
            "flex items-center gap-2",
            isUser ? "justify-end" : "justify-start"
          )}
        >
          <span className="text-foreground text-sm font-semibold">
            {isUser ? "You" : "RAG Assistant"}
          </span>
          {!isUser && message.route && <RouteBadge route={message.route} />}
        </div>

        {!isUser && message.steps && message.steps.length > 0 && (
          <ThinkingSteps steps={message.steps} />
        )}

        <div
          className={cn(
            "prose prose-sm dark:prose-invert max-w-none text-sm leading-relaxed break-words",
            isUser ? "text-foreground/90" : "text-muted-foreground"
          )}
        >
          {isUser ? (
            <p className="whitespace-pre-wrap">{message.content}</p>
          ) : (
            <>
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={markdownComponents}
              >
                {message.content}
              </ReactMarkdown>
              {isStreaming && <StreamingCaret reduced={reduced} />}
            </>
          )}
        </div>

        {/* M10: rich component blocks, after the body. The flag is read INSIDE <ComponentBlock>
            (R9), so this always renders the RAW (opaque) message.components — flag-off pretty-prints
            them, flag-on validates+renders per spec. Invalid blocks drop inside the dispatcher. */}
        {!isUser && message.components && message.components.length > 0 && (
          <div className="space-y-1">
            {message.components.map((spec, i) => (
              <ComponentBlock key={i} spec={spec} index={i} />
            ))}
          </div>
        )}

        {/* Sources (M3) — suppressed when a P6 citation component already shows provenance (R7). */}
        {!isUser && !hasCitation && (
          <SourcesPanel
            sources={message.sources}
            count={message.sourcesCount}
          />
        )}

        {!isUser && message.status !== "streaming" && (
          <MessageActions content={message.content} />
        )}

        {/* Phase 7 (FE-4): per-turn observability stats. Flag-gated + lazy. Degrades to nothing
            when observability is off or no stats were collected (blocking path / flag off).
            The panel itself further guards on its props, so a partial `stats` is safe. */}
        {!isUser && flags.observability && message.stats && (
          <StatsPanel stats={message.stats} />
        )}
      </div>
    </m.div>
  );
}

// Re-render only when this message's identity/content/status/steps/sources change.
export const ChatMessage = React.memo(ChatMessageImpl, (prev, next) => {
  const a = prev.message;
  const b = next.message;
  return (
    a.id === b.id &&
    a.content === b.content &&
    a.status === b.status &&
    a.route === b.route &&
    a.sourcesCount === b.sourcesCount &&
    a.steps === b.steps &&
    a.sources === b.sources &&
    // M10 (R8): addComponent appends a NEW array reference (immutable update), so a late-arriving
    // component block changes identity here and repaints. Without this it wouldn't render.
    a.components === b.components &&
    // Phase 7 (FE-4): setStats writes a NEW stats object each stage/done (immutable update), so
    // identity-compare it here — otherwise the live stats panel wouldn't refresh as stages arrive.
    a.stats === b.stats
  );
});
ChatMessage.displayName = "ChatMessage";
