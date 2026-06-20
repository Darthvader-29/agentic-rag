// features/keys/store/provider.store.ts
//
// Per-conversation provider/model selection for the chat input picker (M7). The selection
// is OPTIONAL on POST /api/chat (docs/09 Appendix C): when `provider` is null the request
// omits both fields and the backend uses its own default (the free Gemini tier). Picking a
// provider seeds that provider's default model; the user may then refine the model.
//
// Persisted so a reload keeps the chosen model. It is intentionally a SEPARATE store from
// the auth/keys data layer — this is pure UI selection state, not server state.
import { create } from "zustand";
import type { Provider } from "@/features/keys/api/keys.schemas";
import { defaultModelFor } from "@/features/keys/models";
import { persistLocal, STORAGE_KEYS } from "@/lib/store/persist";
import { isByokEnabled } from "@/lib/flags";

interface ProviderState {
  /** Selected provider, or null = "use the backend default" (no provider/model sent). */
  provider: Provider | null;
  /** Selected model id; only meaningful when `provider` is set. */
  model: string | null;

  /** Pick a provider; seeds its default model unless an explicit one is supplied. */
  setProvider: (provider: Provider | null, model?: string) => void;
  /** Reset to the backend default (no explicit provider/model). */
  clearSelection: () => void;
}

export const useProviderStore = create<ProviderState>()(
  persistLocal<ProviderState>(STORAGE_KEYS.providerSelection, (set) => ({
    provider: null,
    model: null,

    setProvider: (provider, model) =>
      set({
        provider,
        // null provider → no model; otherwise seed (or use the supplied) model.
        model: provider ? (model ?? defaultModelFor(provider) ?? null) : null,
      }),
    clearSelection: () => set({ provider: null, model: null }),
  }))
);

/**
 * Non-React accessor for the chat API layer (sendMessage / streamChat build the request
 * body outside React). Returns the optional provider/model fields to spread onto the
 * /api/chat body — `{}` when no provider is selected (byte-for-byte today's request).
 *
 * Also returns `{}` when BYOK is disabled (flag off, OR auth off → R24): a value can linger in
 * the persisted store from a prior session, and the picker that would clear it isn't rendered
 * when the surface is hidden, so we never leak a stale provider into the request.
 */
export function getChatModelSelection(): {
  provider?: Provider;
  model?: string;
} {
  if (!isByokEnabled()) return {};
  const { provider, model } = useProviderStore.getState();
  if (!provider) return {};
  return model ? { provider, model } : { provider };
}
