import { FileText, Globe, Zap } from "lucide-react";

export function EmptyState() {
  return (
    <div className="flex flex-col items-center justify-center h-full text-center space-y-6 p-8 animate-in fade-in duration-500">
      <div className="bg-slate-100 p-4 rounded-full dark:bg-slate-800">
        <Zap className="h-8 w-8 text-blue-500 fill-blue-500" />
      </div>
      <div className="space-y-2">
        <h2 className="text-2xl font-bold tracking-tight">RAG Assistant</h2>
        <p className="text-muted-foreground max-w-md mx-auto">
          Upload documents to chat with them, or enable Web Search for live information.
        </p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 max-w-lg w-full mt-8">
        <div className="border rounded-xl p-4 bg-card hover:bg-slate-50 transition-colors cursor-default dark:hover:bg-slate-800">
          <div className="flex items-center gap-2 mb-2 font-semibold text-sm text-blue-600">
            <FileText className="h-4 w-4" />
            Analyze Documents
          </div>
          <p className="text-xs text-muted-foreground">
            "Summarize the quarterly report PDF I just uploaded."
          </p>
        </div>
        
        <div className="border rounded-xl p-4 bg-card hover:bg-slate-50 transition-colors cursor-default dark:hover:bg-slate-800">
          <div className="flex items-center gap-2 mb-2 font-semibold text-sm text-green-600">
            <Globe className="h-4 w-4" />
            Web Search
          </div>
          <p className="text-xs text-muted-foreground">
            "What are the latest features in Next.js 15?"
          </p>
        </div>
      </div>
    </div>
  );
}
