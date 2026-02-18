import { createClient } from '@supabase/supabase-js';

// Unified Supabase project (lever money - wrbrbhuhsaaupqsimkqz)
const supabaseUrl = 'https://wrbrbhuhsaaupqsimkqz.supabase.co';
const supabaseAnonKey = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6IndyYnJiaHVoc2FhdXBxc2lta3F6Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NDY3MTUyOTQsImV4cCI6MjA2MjI5MTI5NH0.Uw9zbPdHo4206oO81jWmDxLwFDR_XA4tPRlosl0NLdM';

export const supabase = createClient(supabaseUrl, supabaseAnonKey);

// API base URL - same origin in production, localhost in dev
export const API_BASE = import.meta.env.VITE_API_BASE_URL || '';

export interface FaturamentoRow {
  id?: number;
  empresa: string;
  data: string; // YYYY-MM-DD
  valor: number;
  source?: string;
  created_at?: string;
  updated_at?: string;
}
