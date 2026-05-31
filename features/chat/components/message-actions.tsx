"use client";

import { Check, Copy, RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useCopyToClipboard } from "@/hooks/use-copy-to-clipboard";
import { useChat } from "@/features/chat/hooks/use-chat";

interface MessageActionsProps {
  content: string;
}

export function MessageActions({ content }: MessageActionsProps) {
  const { copied, copy } = useCopyToClipboard();
  const { retry, isStreaming } = useChat();

  return (
    <div className="flex items-center gap-1 pt-1 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100 motion-reduce:transition-none">
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            aria-label={copied ? "Copied" : "Copy answer"}
            className="text-muted-foreground hover:text-foreground h-7 w-7"
            onClick={() => void copy(content)}
          >
            {copied ? (
              <Check className="h-3.5 w-3.5" />
            ) : (
              <Copy className="h-3.5 w-3.5" />
            )}
          </Button>
        </TooltipTrigger>
        <TooltipContent>{copied ? "Copied" : "Copy answer"}</TooltipContent>
      </Tooltip>

      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            aria-label="Retry"
            disabled={isStreaming}
            className="text-muted-foreground hover:text-foreground h-7 w-7"
            onClick={() => retry()}
          >
            <RotateCcw className="h-3.5 w-3.5" />
          </Button>
        </TooltipTrigger>
        <TooltipContent>Retry</TooltipContent>
      </Tooltip>
    </div>
  );
}
