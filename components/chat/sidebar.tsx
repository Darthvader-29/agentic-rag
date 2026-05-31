"use client";

import { Button } from "@/components/ui/button";
import { ThemeToggle } from "@/components/theme/theme-toggle";
import { Trash2, FileText } from "lucide-react";

interface SidebarProps {
  onClearSession: () => void;
  onToggle?: () => void; // NEW
}

export function Sidebar({ onClearSession, onToggle }: SidebarProps) {
  return (
    <div className="flex h-full w-64 flex-col border-r bg-slate-50/50 p-4 dark:border-slate-800 dark:bg-slate-900/50">
      {/* Header with toggle */}
      <div className="mb-8 flex items-center justify-between px-2">
        <div className="flex items-center gap-2 text-lg font-bold">
          <div className="rounded bg-blue-600 p-1 text-white">
            <FileText className="h-4 w-4" />
          </div>
          RAG Chat
        </div>
        {onToggle && (
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7"
            onClick={onToggle}
          >
            <span className="sr-only">Toggle sidebar</span>
            <svg
              viewBox="0 0 16 16"
              className="h-4 w-4 text-slate-500"
              aria-hidden="true"
            >
              <path
                d="M10.5 3.5L6 8l4.5 4.5"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </Button>
        )}
      </div>

      {/* Main Content centered */}
      <div className="flex flex-1 flex-col items-center justify-center overflow-y-auto">
        <div className="flex w-full flex-col items-center space-y-4">
          {/* Source Code Card */}
          <div className="w-full max-w-[210px] rounded-lg border bg-white p-3 shadow-sm">
            <p className="mb-2 text-center text-xs font-semibold tracking-wider text-slate-500 uppercase">
              Source Code
            </p>

            <div className="space-y-2">
              <a
                href="https://github.com/Darthvader-29/typescript-agentic-rag-frontend"
                target="_blank"
                rel="noopener noreferrer"
              >
                <Button
                  variant="outline"
                  className="w-full justify-center text-xs"
                >
                  Frontend
                </Button>
              </a>

              <a
                href="https://github.com/Darthvader-29/Python-Agentic-RAG-Backend"
                target="_blank"
                rel="noopener noreferrer"
              >
                <Button
                  variant="outline"
                  className="w-full justify-center text-xs"
                >
                  Backend
                </Button>
              </a>
            </div>
          </div>

          {/* About / Contact Message */}
          <div className="w-full px-2">
            <p className="text-muted-foreground mt-2 text-center text-xs leading-relaxed">
              Hello this is my RAG chatbot. This web application has been
              deployed using free available resources so the performance might
              not be at par with enterprise benchmarks. If encountered any
              issues please contact me via mail at{" "}
              <a
                href="mailto:Kanawadeatharva29@gmail.com"
                className="underline underline-offset-2"
              >
                Kanawadeatharva29@gmail.com
              </a>
              . Thank you.
            </p>
          </div>
        </div>
      </div>

      {/* Footer Actions */}
      <div className="flex flex-col gap-2 border-t pt-4 dark:border-slate-800">
        <div className="flex justify-end px-1">
          <ThemeToggle />
        </div>
        <Button
          variant="ghost"
          className="w-full justify-center gap-2 text-red-500 hover:bg-red-50 hover:text-red-600 dark:hover:bg-red-950/30"
          onClick={onClearSession}
        >
          <Trash2 className="h-4 w-4" />
          Reset Session
        </Button>
      </div>
    </div>
  );
}
