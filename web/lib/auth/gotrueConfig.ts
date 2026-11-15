import "server-only";

import type { GotrueConfig } from "@/lib/auth/gotrue";

/**
 * GoTrue connection settings from the server environment, or null when either
 * is missing (callers answer with a help-carrying 503, never a bare 500).
 * Server-only: the anon key is sent as `apikey` and never reaches the browser.
 */
export function readGotrueConfig(): GotrueConfig | null {
  const supabaseUrl = process.env.OPENLAW_SUPABASE_URL?.trim().replace(/\/+$/, "");
  const apiKey = process.env.OPENLAW_SUPABASE_ANON_KEY?.trim();
  if (!supabaseUrl || !apiKey) return null;
  return { supabaseUrl, apiKey };
}
