"use client";

import { Message } from "@/types";
import { cn } from "@/lib/utils";
import { User, Bot, Layers } from "lucide-react";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";

// Markdown & Highlighting imports
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism";

interface ChatMessageProps {
  message: Message;
}

export function ChatMessage({ message }: ChatMessageProps) {
  const isUser = message.role === "user";

  return (
    <div
      className={cn(
        "flex w-full gap-4 rounded-xl p-5 transition-all",
        isUser
          ? "bg-primary/5 flex-row-reverse"
          : "border border-slate-100 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900/50"
      )}
    >
      {/* 1. Avatar */}
      <Avatar className="h-8 w-8 shrink-0 border">
        <AvatarFallback
          className={cn("text-white", isUser ? "bg-blue-600" : "bg-slate-700")}
        >
          {isUser ? <User className="h-4 w-4" /> : <Bot className="h-4 w-4" />}
        </AvatarFallback>
      </Avatar>

      {/* 2. Message Content */}
      <div
        className={cn(
          "min-w-0 flex-1 space-y-2",
          isUser ? "text-right" : "text-left"
        )}
      >
        {/* Header: Name + Badges */}
        <div
          className={cn(
            "flex items-center gap-2",
            isUser ? "justify-end" : "justify-start"
          )}
        >
          <span className="text-sm font-semibold text-slate-800 dark:text-slate-200">
            {isUser ? "You" : "RAG Assistant"}
          </span>

          {!isUser && message.route && (
            <Badge
              variant="outline"
              className="h-5 px-2 text-[10px] font-normal text-slate-500"
            >
              {message.route}
            </Badge>
          )}
        </div>

        {/* Body: Markdown Rendering */}
        <div
          className={cn(
            "prose prose-sm dark:prose-invert max-w-none text-sm leading-relaxed break-words",
            isUser
              ? "text-slate-700 dark:text-slate-300"
              : "text-slate-600 dark:text-slate-400"
          )}
        >
          {isUser ? (
            // User messages are simple text
            <p className="whitespace-pre-wrap">{message.content}</p>
          ) : (
            // AI messages need full Markdown parsing
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={{
                // Custom renderer for code blocks
                code({
                  className,
                  children,
                }: {
                  className?: string;
                  children?: React.ReactNode;
                }) {
                  const match = /language-(\w+)/.exec(className || "");
                  const isInline = !match;

                  return !isInline ? (
                    <div className="my-2 overflow-hidden rounded-md border bg-[#282c34] dark:border-slate-800">
                      <div className="flex justify-between border-b border-slate-700 bg-[#21252b] px-3 py-1 text-xs text-slate-400 select-none">
                        <span>{match?.[1]}</span>
                        <span>Code</span>
                      </div>
                      <SyntaxHighlighter
                        style={oneDark}
                        language={match?.[1]}
                        PreTag="div"
                        customStyle={{
                          margin: 0,
                          padding: "1rem",
                          backgroundColor: "transparent",
                        }}
                      >
                        {String(children).replace(/\n$/, "")}
                      </SyntaxHighlighter>
                    </div>
                  ) : (
                    <code className="rounded bg-slate-100 px-1 py-0.5 font-mono text-xs text-slate-800 dark:bg-slate-800 dark:text-slate-200">
                      {children}
                    </code>
                  );
                },
                // Style links to open in new tab
                a: ({ children, ...props }) => (
                  <a
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-blue-600 hover:underline"
                    {...props}
                  >
                    {children}
                  </a>
                ),
                // Style lists
                ul: ({ children, ...props }) => (
                  <ul className="list-disc space-y-1 pl-4" {...props}>
                    {children}
                  </ul>
                ),
                ol: ({ children, ...props }) => (
                  <ol className="list-decimal space-y-1 pl-4" {...props}>
                    {children}
                  </ol>
                ),
              }}
            >
              {message.content}
            </ReactMarkdown>
          )}
        </div>

        {/* Footer: Sources (Only for RAG) */}
        {!isUser &&
          message.sourcesCount !== undefined &&
          message.sourcesCount > 0 && (
            <div className="text-muted-foreground mt-3 flex items-center gap-2 border-t border-slate-100 pt-3 text-xs dark:border-slate-800">
              <Layers className="h-3 w-3" />
              <span>
                Referenced {message.sourcesCount} chunks from your documents
              </span>
            </div>
          )}
      </div>
    </div>
  );
}
