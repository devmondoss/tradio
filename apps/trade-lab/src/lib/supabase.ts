import { createClient } from '@supabase/supabase-js'

const SUPABASE_URL = 'https://ztdhvmcisjjyhbqlgkzm.supabase.co'
const SUPABASE_KEY =
  'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9' +
  '.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inp0ZGh2bWNpc2pqeWhicWxna3ptIiwicm9sZSI6' +
  'InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3ODk0MTc1MiwiZXhwIjoyMDk0NTE3NzUyfQ' +
  '.sqMh9Jcxrxyg-ZBYWPaNN8DB9kf-KkC7ARPLucItN1Y'

export const supabase = createClient(SUPABASE_URL, SUPABASE_KEY)

export type { MtfTrade, MtfSpotTrade } from './db-types'
