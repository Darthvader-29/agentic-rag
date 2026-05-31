import { FileText, Globe, Zap } from "lucide-react";

export function EmptyState() {
  return (
    <div className="flex h-full flex-col items-center justify-center space-y-6 p-8 text-center">
      <div className="bg-muted rounded-full p-4">
        <Zap className="fill-primary text-primary h-8 w-8" />
      </div>
      <div className="space-y-2">
        <h2 className="text-foreground text-2xl font-bold tracking-tight">
          RAG Assistant
        </h2>
        <p className="text-muted-foreground mx-auto max-w-md">
          Upload documents to chat with them, or enable Web Search for live
          information.
        </p>
      </div>

      <div className="mt-8 grid w-full max-w-lg grid-cols-1 gap-4 md:grid-cols-2">
        <div className="border-border bg-card hover:bg-muted cursor-default rounded-xl border p-4 transition-colors motion-reduce:transition-none">
          <div className="text-primary mb-2 flex items-center gap-2 text-sm font-semibold">
            <FileText className="h-4 w-4" />
            Analyze Documents
          </div>
          <p className="text-muted-foreground text-xs">
            &ldquo;Summarize the quarterly report PDF I just uploaded.&rdquo;
          </p>
        </div>

        <div className="border-border bg-card hover:bg-muted cursor-default rounded-xl border p-4 transition-colors motion-reduce:transition-none">
          <div className="text-chart-2 mb-2 flex items-center gap-2 text-sm font-semibold">
            <Globe className="h-4 w-4" />
            Web Search
          </div>
          <p className="text-muted-foreground text-xs">
            &ldquo;What are the latest features in Next.js 15?&rdquo;
          </p>
        </div>
      </div>
    </div>
  );
}
