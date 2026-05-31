import { Bot } from "lucide-react";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";

export function MessageLoading() {
  return (
    <div className="flex w-full animate-pulse gap-4 rounded-xl border border-slate-100 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900/50">
      {/* Bot Avatar */}
      <Avatar className="h-8 w-8 shrink-0 border">
        <AvatarFallback className="bg-slate-700 text-white">
          <Bot className="h-4 w-4" />
        </AvatarFallback>
      </Avatar>

      <div className="flex-1 space-y-2 pt-1">
        {/* Fake Name */}
        <div className="h-4 w-24 rounded bg-slate-200 dark:bg-slate-800"></div>

        {/* Fake Lines of Text */}
        <div className="h-4 w-3/4 rounded bg-slate-100 dark:bg-slate-800"></div>
        <div className="h-4 w-1/2 rounded bg-slate-100 dark:bg-slate-800"></div>
      </div>
    </div>
  );
}
