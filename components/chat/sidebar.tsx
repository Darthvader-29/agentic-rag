"use client";

import { Button } from "@/components/ui/button";
import { Trash2, FileText } from "lucide-react";

interface SidebarProps {
  onClearSession: () => void;
  onToggle?: () => void; // NEW
}

export function Sidebar({ onClearSession, onToggle }: SidebarProps) {
  return (
    <div className="w-64 border-r h-full bg-slate-50/50 flex flex-col p-4 dark:bg-slate-900/50 dark:border-slate-800">
      {/* Header with toggle */}
      <div className="flex items-center justify-between mb-8 px-2">
        <div className="flex items-center gap-2 font-bold text-lg">
          <div className="bg-blue-600 p-1 rounded text-white">
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
      <div className="flex-1 flex flex-col items-center justify-center overflow-y-auto">
        <div className="space-y-4 w-full flex flex-col items-center">
          {/* Source Code Card */}
          <div className="w-full max-w-[210px] rounded-lg border bg-white shadow-sm p-3">
            <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2 text-center">
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
            <p className="mt-2 text-xs text-muted-foreground leading-relaxed text-center">
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
      <div className="border-t pt-4 dark:border-slate-800 flex justify-center">
        <Button
          variant="ghost"
          className="w-full justify-center gap-2 text-red-500 hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-950/30"
          onClick={onClearSession}
        >
          <Trash2 className="h-4 w-4" />
          Reset Session
        </Button>
      </div>
    </div>
  );
}
