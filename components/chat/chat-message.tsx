"use client";

import { Message } from "@/types";
import { cn } from "@/lib/utils";
import { User, Bot, Layers, FileText } from "lucide-react";
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
        "flex w-full gap-4 p-5 rounded-xl transition-all",
        isUser ? "bg-primary/5 flex-row-reverse" : "bg-white border border-slate-100 shadow-sm dark:bg-slate-900/50 dark:border-slate-800"
      )}
    >
      {/* 1. Avatar */}
      <Avatar className="h-8 w-8 border shrink-0">
        <AvatarFallback
          className={cn(
            "text-white",
            isUser ? "bg-blue-600" : "bg-slate-700"
          )}
        >
          {isUser ? <User className="h-4 w-4" /> : <Bot className="h-4 w-4" />}
        </AvatarFallback>
      </Avatar>

      {/* 2. Message Content */}
      <div className={cn("flex-1 space-y-2 min-w-0", isUser ? "text-right" : "text-left")}>
        
        {/* Header: Name + Badges */}
        <div className={cn("flex items-center gap-2", isUser ? "justify-end" : "justify-start")}>
          <span className="font-semibold text-sm text-slate-800 dark:text-slate-200">
            {isUser ? "You" : "RAG Assistant"}
          </span>
          
          {!isUser && message.route && (
            <Badge variant="outline" className="text-[10px] px-2 h-5 text-slate-500 font-normal">
              {message.route}
            </Badge>
          )}
        </div>

        {/* Body: Markdown Rendering */}
        <div className={cn(
          "text-sm leading-relaxed prose prose-sm max-w-none break-words dark:prose-invert",
          isUser ? "text-slate-700 dark:text-slate-300" : "text-slate-600 dark:text-slate-400"
        )}>
          {isUser ? (
            // User messages are simple text
            <p className="whitespace-pre-wrap">{message.content}</p>
          ) : (
            // AI messages need full Markdown parsing
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={{
                // Custom renderer for code blocks
                code({ className, children, ...props }: any) {
                  const match = /language-(\w+)/.exec(className || "");
                  const isInline = !match;
                  
                  return !isInline ? (
                    <div className="rounded-md overflow-hidden my-2 border bg-[#282c34] dark:border-slate-800">
                      <div className="bg-[#21252b] text-slate-400 text-xs px-3 py-1 border-b border-slate-700 flex justify-between select-none">
                        <span>{match?.[1]}</span>
                        <span>Code</span>
                      </div>
                      <SyntaxHighlighter
                        style={oneDark}
                        language={match?.[1]}
                        PreTag="div"
                        customStyle={{ margin: 0, padding: "1rem", backgroundColor: "transparent" }}
                        {...props}
                      >
                        {String(children).replace(/\n$/, "")}
                      </SyntaxHighlighter>
                    </div>
                  ) : (
                    <code className="bg-slate-100 text-slate-800 px-1 py-0.5 rounded text-xs font-mono dark:bg-slate-800 dark:text-slate-200" {...props}>
                      {children}
                    </code>
                  );
                },
                // Style links to open in new tab
                a: ({ node, ...props }) => (
                  <a target="_blank" rel="noopener noreferrer" className="text-blue-600 hover:underline" {...props} />
                ),
                // Style lists
                ul: ({ node, ...props }) => <ul className="list-disc pl-4 space-y-1" {...props} />,
                ol: ({ node, ...props }) => <ol className="list-decimal pl-4 space-y-1" {...props} />,
              }}
            >
              {message.content}
            </ReactMarkdown>
          )}
        </div>

        {/* Footer: Sources (Only for RAG) */}
        {!isUser && message.sourcesCount !== undefined && message.sourcesCount > 0 && (
          <div className="mt-3 pt-3 border-t border-slate-100 flex items-center gap-2 text-xs text-muted-foreground dark:border-slate-800">
            <Layers className="h-3 w-3" />
            <span>Referenced {message.sourcesCount} chunks from your documents</span>
          </div>
        )}
      </div>
    </div>
  );
}
