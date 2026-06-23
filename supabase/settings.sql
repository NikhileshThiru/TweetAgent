-- ============================================================================
-- app_settings — a single row holding your persistent "steering" instructions
-- (always injected into every generation). Run this once in the SQL Editor.
-- Safe to re-run.
-- ============================================================================

create table if not exists public.app_settings (
  id          smallint primary key default 1,
  steering    text not null default '',
  updated_at  timestamptz not null default now(),
  constraint  app_settings_single_row check (id = 1)
);

-- Seed the single row.
insert into public.app_settings (id, steering)
values (1, '')
on conflict (id) do nothing;

-- Keep updated_at fresh (reuses the function created in schema.sql).
drop trigger if exists app_settings_set_updated_at on public.app_settings;
create trigger app_settings_set_updated_at
  before update on public.app_settings
  for each row
  execute function public.set_updated_at();

-- RLS: same owner-only gate as tweet_drafts. The cron/function uses the service
-- key (bypasses RLS); the UI reads/writes it as the logged-in owner.
alter table public.app_settings enable row level security;

drop policy if exists "Owner settings access" on public.app_settings;
create policy "Owner settings access"
  on public.app_settings
  for all
  to authenticated
  using      ( (auth.jwt() ->> 'email') = 'you@example.com' )
  with check ( (auth.jwt() ->> 'email') = 'you@example.com' );
