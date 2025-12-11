"use client";

import { useState, KeyboardEvent, useRef } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Globe, ArrowUp, Paperclip, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { api } from "@/services/api";
import { toast } from "sonner";

interface ChatInputProps {
  isLoading: boolean;
  onSend: (message: string, webSearch: boolean) => void;
  onFileUploaded?: (fileName: string) => void; // NEW
}

export function ChatInput({ isLoading, onSend, onFileUploaded }: ChatInputProps) {
  const [input, setInput] = useState("");
  const [webSearch, setWebSearch] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleSend = () => {
    if (!input.trim() || isLoading) return;
    onSend(input, webSearch);
    setInput("");
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    setIsUploading(true);
    try {
      await api.uploadFile(file);
      toast.success(`${file.name} uploaded`);
      onFileUploaded?.(file.name); // notify parent to add a message
    } catch (error) {
      toast.error("Upload failed");
    } finally {
      setIsUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  return (
    <div className="p-4 bg-background border-t dark:border-slate-800">
      <div className="max-w-4xl mx-auto space-y-2">
        <div className="relative flex items-center p-1 border rounded-full bg-background shadow-sm focus-within:ring-1 focus-within:ring-ring dark:border-slate-800">
          {/* Left buttons */}
          <div className="flex items-center gap-1 pl-1">
            <input
              type="file"
              ref={fileInputRef}
              className="hidden"
              onChange={handleFileUpload}
              accept=".pdf,.docx,.txt"
            />
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8 text-muted-foreground hover:text-foreground rounded-full"
              onClick={() => fileInputRef.current?.click()}
              disabled={isUploading || isLoading}
              title="Upload document"
            >
              {isUploading ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Paperclip className="h-4 w-4" />
              )}
            </Button>

            <Button
              variant="ghost"
              size="icon"
              className={cn(
                "h-8 w-8 transition-colors rounded-full",
                webSearch
                  ? "text-blue-500 bg-blue-50 hover:bg-blue-100 hover:text-blue-600"
                  : "text-muted-foreground hover:text-foreground"
              )}
              onClick={() => setWebSearch(!webSearch)}
              title={webSearch ? "Web search enabled" : "Web search disabled"}
            >
              <Globe className="h-4 w-4" />
            </Button>
          </div>

          {/* Text input */}
          <Textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask anything..."
            className="min-h-[40px] max-h-[200px] resize-none border-0 shadow-none focus-visible:ring-0 px-3 py-2 bg-transparent flex-1 leading-6"
            style={{ height: "40px" }}
            disabled={isLoading}
          />

          {/* Send */}
          <Button
            size="icon"
            onClick={handleSend}
            disabled={isLoading || !input.trim()}
            className="h-8 w-8 rounded-full shrink-0 mr-1"
          >
            <ArrowUp className="h-4 w-4" />
          </Button>
        </div>

        <div className="text-[10px] text-center text-muted-foreground">
          AI can make mistakes. Check important info.
        </div>
      </div>
    </div>
  );
}
