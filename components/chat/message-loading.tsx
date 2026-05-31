import { Bot } from "lucide-react";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Skeleton } from "@/components/ui/skeleton";

export function MessageLoading() {
  return (
    <div
      className="border-border bg-card flex w-full gap-4 rounded-xl border p-5 shadow-sm"
      role="status"
      aria-live="polite"
      aria-label="Assistant is thinking"
    >
      <Avatar className="border-border h-8 w-8 shrink-0 border">
        <AvatarFallback className="bg-muted text-muted-foreground">
          <Bot className="h-4 w-4" />
        </AvatarFallback>
      </Avatar>
      <div className="flex-1 space-y-2 pt-1">
        <Skeleton className="h-4 w-24" />
        <Skeleton className="h-4 w-3/4" />
        <Skeleton className="h-4 w-1/2" />
      </div>
      <span className="sr-only">Assistant is generating a response…</span>
    </div>
  );
}
