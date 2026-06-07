"use client";

import * as React from "react";
import dynamic from "next/dynamic";
import { Check, Copy } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { useCopyToClipboard } from "@/hooks/use-copy-to-clipboard";

// Lazy, client-only. The highlighter (~½MB w/ Prism + theme) is never in the
// first-load bundle; it loads only when a fenced code block actually mounts.
const SyntaxHighlighter = dynamic(
  async () => {
    const mod = await import("react-syntax-highlighter");
    return mod.Prism;
  },
  {
    ssr: false,
    loading: () => null, // the <pre> fallback below covers SSR + load gap
  }
);

let cachedTheme: Record<string, React.CSSProperties> | null = null;
async function loadTheme() {
  if (cachedTheme) return cachedTheme;
  const mod = await import("react-syntax-highlighter/dist/esm/styles/prism");
  cachedTheme = mod.oneDark;
  return cachedTheme;
}

interface CodeBlockProps {
  language: string | undefined;
  value: string;
}

export function CodeBlock({ language, value }: CodeBlockProps) {
  const { copied, copy } = useCopyToClipboard({ showToast: false });
  const [theme, setTheme] = React.useState<Record<
    string,
    React.CSSProperties
  > | null>(null);

  React.useEffect(() => {
    let active = true;
    loadTheme().then((t) => active && setTheme(t));
    return () => {
      active = false;
    };
  }, []);

  return (
    <div className="border-border bg-muted my-3 overflow-hidden rounded-md border">
      <div className="border-border bg-muted/60 flex items-center justify-between border-b px-3 py-1.5 select-none">
        <span className="text-muted-foreground font-mono text-xs">
          {language ?? "text"}
        </span>
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          aria-label={copied ? "Copied" : "Copy code"}
          className={cn("text-muted-foreground hover:text-foreground h-6 w-6")}
          onClick={() => void copy(value)}
        >
          {copied ? (
            <Check className="h-3.5 w-3.5" />
          ) : (
            <Copy className="h-3.5 w-3.5" />
          )}
        </Button>
      </div>

      {theme ? (
        <SyntaxHighlighter
          language={language}
          style={theme}
          PreTag="div"
          customStyle={{
            margin: 0,
            padding: "1rem",
            background: "transparent",
            fontSize: "0.8125rem",
          }}
          codeTagProps={{ className: "font-mono" }}
        >
          {value}
        </SyntaxHighlighter>
      ) : (
        <pre className="overflow-x-auto p-4 text-[0.8125rem] leading-relaxed">
          <code className="text-foreground font-mono">{value}</code>
        </pre>
      )}
    </div>
  );
}
