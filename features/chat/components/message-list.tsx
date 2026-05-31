"use client";

import { AnimatePresence } from "framer-motion";
import { ChatMessage } from "@/components/chat/chat-message";
import { MessageLoading } from "@/components/chat/message-loading";
import type { Message } from "@/types";

interface MessageListProps {
  messages: Message[];
  isLoading: boolean;
}

export function MessageList({ messages, isLoading }: MessageListProps) {
  return (
    <>
      <AnimatePresence initial={false} mode="popLayout">
        {messages.map((message) => (
          <ChatMessage key={message.id} message={message} />
        ))}
      </AnimatePresence>
      <AnimatePresence>
        {isLoading && <MessageLoading key="loading" />}
      </AnimatePresence>
    </>
  );
}
