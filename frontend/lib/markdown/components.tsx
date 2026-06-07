"use client";

import { type Components } from "react-markdown";

import { CodeBlock } from "@/features/chat/components/code-block";

// Module-scope stable map — ReactMarkdown does not rebuild its renderer tree
// on each streamed token (M9) or parent re-render.
export const markdownComponents: Components = {
  code({ className, children, ...props }) {
    const match = /language-(\w+)/.exec(className ?? "");
    const isInline = !match;
    if (isInline) {
      return (
        <code
          className="bg-muted text-foreground rounded px-1 py-0.5 font-mono text-xs"
          {...props}
        >
          {children}
        </code>
      );
    }
    return (
      <CodeBlock
        language={match?.[1]}
        value={String(children).replace(/\n$/, "")}
      />
    );
  },
  a: ({ children, ...props }) => (
    <a
      target="_blank"
      rel="noopener noreferrer"
      className="text-primary underline-offset-2 hover:underline"
      {...props}
    >
      {children}
    </a>
  ),
  ul: ({ ...props }) => <ul className="list-disc space-y-1 pl-4" {...props} />,
  ol: ({ ...props }) => (
    <ol className="list-decimal space-y-1 pl-4" {...props} />
  ),
};
