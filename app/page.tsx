"use client";

import { useState, useEffect, useRef } from "react";
import { v4 as uuidv4 } from "uuid";
import { Message } from "@/types";
import { api } from "@/services/api";

import { Sidebar } from "@/components/chat/sidebar";
import { ChatMessage } from "@/components/chat/chat-message";
import { ChatInput } from "@/components/chat/chat-input";
import { EmptyState } from "@/components/chat/empty-state";
import { MessageLoading } from "@/components/chat/message-loading";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Button } from "@/components/ui/button";
import { Menu } from "lucide-react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";

export default function Home() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isSidebarOpen, setIsSidebarOpen] = useState(true);
  const scrollRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [messages, isLoading]);

  // Cleanup on tab close / refresh
  useEffect(() => {
    const handleBeforeUnload = () => {
      const sessionId = api.getSessionId();
      if (!sessionId) return;

      const payload = JSON.stringify({
        session_id: sessionId,
        file_keys: [],
      });

      navigator.sendBeacon(
        `${API_BASE_URL}/cleanup`,
        new Blob([payload], { type: "application/json" })
      );
    };

    window.addEventListener("beforeunload", handleBeforeUnload);
    return () => window.removeEventListener("beforeunload", handleBeforeUnload);
  }, []);

  const handleSendMessage = async (text: string, webSearch: boolean) => {
    const userMsg: Message = {
      id: uuidv4(),
      role: "user",
      content: text,
      timestamp: new Date(),
    };
    setMessages((prev) => [...prev, userMsg]);
    setIsLoading(true);

    try {
      const response = await api.sendMessage(text, webSearch);

      const aiMsg: Message = {
        id: uuidv4(),
        role: "assistant",
        content: response.answer,
        route: response.route,
        sourcesCount: response.context_count,
        timestamp: new Date(),
      };
      setMessages((prev) => [...prev, aiMsg]);
    } catch (err: unknown) {
      console.error(err);

      const errorText =
        err instanceof Error
          ? err.message
          : "The AI service returned an error. Please try again later.";

      const errorMsg: Message = {
        id: uuidv4(),
        role: "assistant",
        content: errorText,
        timestamp: new Date(),
        route: "ERROR",
      };
      setMessages((prev) => [...prev, errorMsg]);
    } finally {
      setIsLoading(false);
    }
  };

  const handleClearSession = async () => {
    await api.clearSession();
    setMessages([]);
    toast.success("Chat history cleared");
  };

  return (
    <div className="flex h-screen w-full overflow-hidden bg-slate-50 dark:bg-slate-950">
      {/* Sidebar with transition */}
      <div
        className={cn(
          "overflow-hidden transition-all duration-300 ease-in-out",
          isSidebarOpen ? "w-64 opacity-100" : "w-0 opacity-0"
        )}
      >
        <Sidebar
          onClearSession={handleClearSession}
          onToggle={() => setIsSidebarOpen(false)}
        />
      </div>

      {/* Main Chat Area */}
      <div className="bg-background relative my-0 mr-0 flex h-full flex-1 flex-col overflow-hidden rounded-l-2xl border-l border-slate-100 shadow-xl dark:border-slate-800 dark:shadow-none">
        {/* Open button – only when sidebar is closed */}
        {!isSidebarOpen && (
          <div className="absolute top-4 left-4 z-10">
            <Button
              variant="ghost"
              size="icon"
              onClick={() => setIsSidebarOpen(true)}
              className="hover:bg-slate-100 dark:hover:bg-slate-800"
            >
              <Menu className="h-5 w-5 text-slate-500" />
            </Button>
          </div>
        )}

        <ScrollArea className="max-h-[calc(100vh-80px)] flex-1 p-4">
          <div className="mx-auto max-w-4xl space-y-6 pt-10 pb-10">
            {messages.length === 0 ? (
              <div className="mt-10">
                <EmptyState />
              </div>
            ) : (
              <>
                {messages.map((msg) => (
                  <ChatMessage key={msg.id} message={msg} />
                ))}
                {isLoading && <MessageLoading />}
              </>
            )}
            <div ref={scrollRef} />
          </div>
        </ScrollArea>

        <ChatInput
          isLoading={isLoading}
          onSend={handleSendMessage}
          onFileUploaded={(fileName) => {
            const msg: Message = {
              id: uuidv4(),
              role: "assistant",
              content: `📄 "${fileName}" uploaded and queued for ingestion.`,
              timestamp: new Date(),
            };
            setMessages((prev) => [...prev, msg]);
          }}
        />
      </div>
    </div>
  );
}
