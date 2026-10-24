import { createClient, SupabaseClient } from "@supabase/supabase-js";

const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
const key = process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY;

// A plain browser client: the console has no login, and the tables are demo data.
// null when the env is missing, so the page can say which variable to set instead of crashing.
export const supabase: SupabaseClient | null = url && key ? createClient(url, key) : null;
