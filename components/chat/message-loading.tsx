import { Bot } from "lucide-react";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";

export function MessageLoading() {
  return (
    <div className="flex w-full gap-4 p-5 rounded-xl bg-white border border-slate-100 shadow-sm animate-pulse dark:bg-slate-900/50 dark:border-slate-800">
      {/* Bot Avatar */}
      <Avatar className="h-8 w-8 border shrink-0">
        <AvatarFallback className="bg-slate-700 text-white">
          <Bot className="h-4 w-4" />
        </AvatarFallback>
      </Avatar>

      <div className="flex-1 space-y-2 pt-1">
        {/* Fake Name */}
        <div className="h-4 w-24 bg-slate-200 rounded dark:bg-slate-800"></div>
        
        {/* Fake Lines of Text */}
        <div className="h-4 w-3/4 bg-slate-100 rounded dark:bg-slate-800"></div>
        <div className="h-4 w-1/2 bg-slate-100 rounded dark:bg-slate-800"></div>
      </div>
    </div>
  );
}
