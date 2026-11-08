import { createClient } from '@supabase/supabase-js'

/**
 * The dashboard's one Supabase client, for signing handlers in. The URL and the publishable key are meant to be public;
 * Vite sends only variables whose names start with VITE_, so the handlers' password never reaches the browser.
 */
export const supabase = createClient(
  import.meta.env.VITE_SUPABASE_URL as string,
  import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY as string,
)
