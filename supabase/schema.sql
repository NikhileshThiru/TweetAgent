-- ============================================================================
-- Tweet Draft Generator — Supabase schema
-- Run this in the Supabase SQL Editor (Dashboard → SQL Editor → New query).
-- Safe to re-run: uses "if not exists" / "or replace" where possible.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Table
-- ----------------------------------------------------------------------------
create table if not exists public.tweet_drafts (
  id          uuid primary key default gen_random_uuid(),
  text        text not null,

  -- pending  → freshly generated, awaiting review
  -- approved → you've okayed it but haven't posted to X yet
  -- posted   → you've actually posted it (keeps Approved tab from growing forever)
  -- trashed  → rejected
  status      text not null default 'pending'
              check (status in ('pending', 'approved', 'posted', 'trashed')),

  topic_hint  text,                                   -- which angle the LLM was nudged toward
  model       text,                                   -- e.g. 'gemini-2.5-flash' — lets you A/B providers
  feedback    text,                                   -- your per-draft note; fed back into future prompts
  image_url   text,                                   -- article preview image (via Microlink) to attach when posting

  -- char_count is computed by the DB so the UI can show "X / 280" with no client logic.
  char_count  int generated always as (char_length(text)) stored,

  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

-- For anyone who created the table before these columns existed:
alter table public.tweet_drafts add column if not exists feedback text;
alter table public.tweet_drafts add column if not exists image_url text;

-- Fast lookups for the three tabs and for "fetch my last N drafts" (dedup context).
create index if not exists tweet_drafts_status_created_idx
  on public.tweet_drafts (status, created_at desc);

create index if not exists tweet_drafts_created_idx
  on public.tweet_drafts (created_at desc);

-- ----------------------------------------------------------------------------
-- Keep updated_at current on every UPDATE
-- ----------------------------------------------------------------------------
create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists tweet_drafts_set_updated_at on public.tweet_drafts;
create trigger tweet_drafts_set_updated_at
  before update on public.tweet_drafts
  for each row
  execute function public.set_updated_at();

-- ----------------------------------------------------------------------------
-- Row Level Security
--
-- The React UI ships the ANON key inside its public JS bundle, so RLS is the
-- ONLY thing standing between your drafts and anyone who finds the URL.
--
-- Model:
--   • The generator function uses the SERVICE ROLE key, which bypasses RLS
--     entirely, so its inserts always work regardless of these policies.
--   • The UI uses the ANON key + a magic-link session. The policy below grants
--     access ONLY to a logged-in user whose email matches yours. Anyone else can
--     sign in with their own email but the policy returns zero rows for them and
--     blocks all writes.
--
-- 👉 Change the email below if you ever want a different owner.
-- ----------------------------------------------------------------------------
alter table public.tweet_drafts enable row level security;

drop policy if exists "Owner full access" on public.tweet_drafts;
create policy "Owner full access"
  on public.tweet_drafts
  for all
  to authenticated
  using      ( (auth.jwt() ->> 'email') = 'you@example.com' )
  with check ( (auth.jwt() ->> 'email') = 'you@example.com' );

-- Note: we intentionally grant NOTHING to the `anon` role. An unauthenticated
-- visitor (no magic-link session) sees nothing and can write nothing.
