-- ============================================================================
-- Supabase Cron — hourly trickle that calls the Vercel generator function.
-- This REPLACES GitHub Actions. Free on Supabase (pg_cron + pg_net).
--
-- Run this once in the Supabase SQL Editor AFTER you've deployed the UI to
-- Vercel (so you know your domain) and set the function's env vars.
--
-- Replace the two placeholders below:
--   <YOUR_VERCEL_DOMAIN>  e.g. tweet-drafts.vercel.app   (no https://, no slash)
--   <YOUR_CRON_SECRET>    the same random string you set as CRON_SECRET in Vercel
-- ============================================================================

create extension if not exists pg_cron;
create extension if not exists pg_net;

-- pg_cron runs in UTC and fires every hour at :00. The function itself only
-- generates during 8am–midnight Atlanta time (DST-aware) and exits instantly
-- otherwise, so scheduling 24x/day is fine — off-hours calls are ~free no-ops.
select cron.schedule(
  'tweet-drafts-hourly',
  '0 * * * *',
  $$
  select net.http_post(
    url     := 'https://<YOUR_VERCEL_DOMAIN>/api/generate',
    headers := jsonb_build_object(
                 'Content-Type', 'application/json',
                 'x-cron-secret', '<YOUR_CRON_SECRET>'
               ),
    body    := jsonb_build_object('mode', 'single'),
    timeout_milliseconds := 60000
  );
  $$
);

-- Useful management commands:
--   select * from cron.job;                          -- list scheduled jobs
--   select cron.unschedule('tweet-drafts-hourly');   -- stop the trickle
--   select * from net._http_response order by created desc limit 5;  -- recent call results
