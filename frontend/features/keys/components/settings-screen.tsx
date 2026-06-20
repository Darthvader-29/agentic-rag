"use client";

import Link from "next/link";
import { ArrowLeft, UserPlus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { isByokEnabled } from "@/lib/flags";
import { useAuth } from "@/features/auth/hooks/use-auth";
import { ApiKeysForm } from "@/features/keys/components/api-keys-form";

/**
 * Settings surface for BYOK key management (M7). Gated on BYOK + auth (R24):
 *  - BYOK disabled (flag off, OR auth off) → "not available" notice (route stays reachable but
 *    inert). With auth off, key-saving is impossible, so we DON'T show a "Sign in to add keys"
 *    CTA that `/login` can't fulfil — the notice is the honest dead-end-free state.
 *  - Not authenticated (auth on, no token yet) → sign-in link (the AuthGuard normally mints a
 *    guest, but if a guest hasn't minted we surface a link rather than firing the Bearer-guarded
 *    keys query for nobody).
 *  - Guest → key form + a "Register to keep your keys" nudge (guest tokens are ephemeral).
 *  - Registered → key form.
 */
export function SettingsScreen() {
  const { isAuthenticated, isGuest } = useAuth();

  return (
    <div className="bg-background min-h-screen">
      <div className="mx-auto max-w-2xl px-4 py-8">
        <div className="mb-6 flex items-center gap-3">
          <Button asChild variant="ghost" size="icon" aria-label="Back to chat">
            <Link href="/">
              <ArrowLeft className="h-4 w-4" />
            </Link>
          </Button>
          <div>
            <h1 className="text-xl font-semibold">API keys</h1>
            <p className="text-muted-foreground text-sm">
              Bring your own key (BYOK) for private, unlimited use across
              Gemini, OpenAI, and Anthropic.
            </p>
          </div>
        </div>

        {!isByokEnabled() ? (
          <p className="text-muted-foreground text-sm">
            Key management isn&apos;t available yet.
          </p>
        ) : !isAuthenticated ? (
          <div className="border-border space-y-3 rounded-lg border p-6 text-center">
            <p className="text-muted-foreground text-sm">
              Sign in to add and manage your API keys.
            </p>
            <Button asChild>
              <Link href="/login?next=/settings">Sign in</Link>
            </Button>
          </div>
        ) : (
          <div className="space-y-4">
            {isGuest && (
              <div className="border-border bg-muted/40 flex items-center justify-between gap-3 rounded-lg border p-3">
                <p className="text-muted-foreground text-sm">
                  You&apos;re a guest — register to keep your keys across
                  sessions.
                </p>
                <Button asChild variant="outline" size="sm" className="gap-1">
                  <Link href="/register">
                    <UserPlus className="h-4 w-4" />
                    Register
                  </Link>
                </Button>
              </div>
            )}
            <ApiKeysForm />
          </div>
        )}
      </div>
    </div>
  );
}
