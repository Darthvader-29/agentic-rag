"use client";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { RouteType } from "@/types";

type Variant = React.ComponentProps<typeof Badge>["variant"];

const ROUTE_MAP: Record<
  RouteType,
  { label: string; variant: Variant; className?: string }
> = {
  RAG: { label: "RAG", variant: "secondary" },
  WEB: { label: "Web", variant: "secondary" },
  DIRECT: { label: "Direct", variant: "outline" },
  "WEB+RAG": { label: "Web + RAG", variant: "secondary" },
  "DIRECT+WEB": { label: "Direct + Web", variant: "outline" },
  "DIRECT+RAG": { label: "Direct + RAG", variant: "outline" },
  ERROR: { label: "Error", variant: "destructive" },
};

interface RouteBadgeProps {
  route: RouteType;
  className?: string;
}

export function RouteBadge({ route, className }: RouteBadgeProps) {
  const cfg = ROUTE_MAP[route] ?? ROUTE_MAP.DIRECT;
  return (
    <Badge
      variant={cfg.variant}
      className={cn(
        "h-5 px-2 text-[10px] font-normal",
        cfg.className,
        className
      )}
      aria-label={`Route: ${cfg.label}`}
    >
      {cfg.label}
    </Badge>
  );
}
