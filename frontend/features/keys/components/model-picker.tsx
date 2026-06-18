"use client";

import { useEffect, useMemo } from "react";
import Link from "next/link";
import { Check, ChevronDown, KeyRound, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";
import { isByokEnabled } from "@/lib/flags";
import { useProviderStore } from "@/features/keys/store/provider.store";
import { useApiKeys } from "@/features/keys/hooks/use-api-keys";
import { PROVIDERS, modelLabel } from "@/features/keys/models";
import type { Provider } from "@/features/keys/api/keys.schemas";

/**
 * Per-conversation provider/model picker (M7), rendered next to the chat input. The
 * selection is OPTIONAL on /api/chat: "Auto" (no provider) lets the backend use its default
 * (free Gemini tier); picking a model sends `provider` + `model`. State lives in the
 * persisted provider store, so a reload keeps the choice.
 *
 * Renders nothing unless BYOK is on AND auth is on — BYOK key-saving is Bearer-guarded, so
 * with auth off the picker can't deliver a usable provider selection (R24).
 *
 * Unowned providers (no stored key) are DISABLED with an "Add key" affordance (R25): selecting
 * a provider you have no key for produces persistently-broken turns and loses the free tier. If
 * the currently-selected provider's key is removed, the selection falls back to Auto.
 */
export function ModelPicker() {
  const provider = useProviderStore((s) => s.provider);
  const model = useProviderStore((s) => s.model);
  const setProvider = useProviderStore((s) => s.setProvider);
  const clearSelection = useProviderStore((s) => s.clearSelection);

  const { keys } = useApiKeys();
  const ownedProviders = useMemo(
    () => new Set<Provider>(keys.map((k) => k.provider)),
    [keys]
  );

  // If the selected provider's key was removed (or never existed), drop back to Auto so the
  // next turn can't fail against a provider the user has no key for.
  useEffect(() => {
    if (provider && !ownedProviders.has(provider)) clearSelection();
  }, [provider, ownedProviders, clearSelection]);

  if (!isByokEnabled()) return null;

  const current =
    provider && model ? modelLabel(provider, model) : "Auto (free tier)";

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="text-muted-foreground hover:text-foreground h-7 gap-1 rounded-full px-2 text-xs"
          aria-label="Select model"
        >
          <Sparkles className="h-3.5 w-3.5" />
          <span className="max-w-[10rem] truncate">{current}</span>
          <ChevronDown className="h-3 w-3 opacity-60" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-56">
        <DropdownMenuItem onSelect={() => clearSelection()}>
          <span className="flex-1">Auto (free Gemini tier)</span>
          {!provider && <Check className="h-4 w-4" />}
        </DropdownMenuItem>
        {PROVIDERS.map((p) => {
          const owned = ownedProviders.has(p.provider);
          return (
            <div key={p.provider}>
              <DropdownMenuSeparator />
              <DropdownMenuLabel className="text-muted-foreground flex items-center justify-between gap-2 text-xs">
                <span>{p.label}</span>
                {!owned && (
                  // Affordance to add the missing key — routes to settings to store one.
                  <Link
                    href="/settings"
                    className="text-foreground inline-flex items-center gap-1 font-medium underline underline-offset-2"
                  >
                    <KeyRound className="h-3 w-3" aria-hidden="true" />
                    Add key
                  </Link>
                )}
              </DropdownMenuLabel>
              {p.models.map((m) => {
                const selected = provider === p.provider && model === m.id;
                return (
                  <DropdownMenuItem
                    key={m.id}
                    // Disabled until the provider has a stored key (radix sets data-disabled →
                    // the item is non-interactive + dimmed, and onSelect won't fire).
                    disabled={!owned}
                    onSelect={() => setProvider(p.provider, m.id)}
                    className={cn(selected && "font-medium")}
                    aria-label={
                      owned
                        ? m.label
                        : `${m.label} (add a ${p.label} key to use)`
                    }
                  >
                    <span className="flex-1">{m.label}</span>
                    {selected && <Check className="h-4 w-4" />}
                  </DropdownMenuItem>
                );
              })}
            </div>
          );
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
