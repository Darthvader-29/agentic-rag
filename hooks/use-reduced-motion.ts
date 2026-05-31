"use client";

import { useSyncExternalStore } from "react";

const QUERY = "(prefers-reduced-motion: reduce)";

function subscribe(onChange: () => void): () => void {
  if (typeof window === "undefined" || !window.matchMedia) {
    return () => {};
  }
  const mql = window.matchMedia(QUERY);
  // Safari < 14 used addListener/removeListener; modern browsers use add/removeEventListener.
  if (typeof mql.addEventListener === "function") {
    mql.addEventListener("change", onChange);
    return () => mql.removeEventListener("change", onChange);
  }
  mql.addListener(onChange);
  return () => mql.removeListener(onChange);
}

function getSnapshot(): boolean {
  if (typeof window === "undefined" || !window.matchMedia) return false;
  return window.matchMedia(QUERY).matches;
}

function getServerSnapshot(): boolean {
  // On the server we cannot know the user's preference; assume motion is allowed.
  // The client snapshot reconciles immediately after hydration without layout shift,
  // because reduced-motion only gates animation, not layout.
  return false;
}

/**
 * SSR-safe `prefers-reduced-motion` hook. Returns `true` when the user has requested
 * reduced motion. Subscribes to live OS-level changes and cleans up on unmount.
 */
export function useReducedMotion(): boolean {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}
