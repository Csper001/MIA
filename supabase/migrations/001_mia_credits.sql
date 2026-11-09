-- Mia Home AI credit ledger (apply only after reviewing in Supabase SQL editor).
-- User-owned balances are not directly writable by browsers.
create table if not exists public.mia_credit_accounts (
  user_id uuid primary key references auth.users(id) on delete cascade,
  free_credits integer not null default 1 check (free_credits between 0 and 1),
  paid_credits integer not null default 0 check (paid_credits >= 0),
  created_at timestamptz not null default now()
);
alter table public.mia_credit_accounts enable row level security;
revoke all on public.mia_credit_accounts from anon, authenticated;
grant select on public.mia_credit_accounts to authenticated;
drop policy if exists mia_read_own_credits on public.mia_credit_accounts;
create policy mia_read_own_credits on public.mia_credit_accounts
  for select to authenticated using (auth.uid() = user_id);

create table if not exists public.mia_stripe_purchases (
  session_id text primary key,
  user_id uuid not null references auth.users(id) on delete cascade,
  stripe_event_id text not null unique,
  credits integer not null check (credits > 0),
  created_at timestamptz not null default now()
);
alter table public.mia_stripe_purchases enable row level security;
revoke all on public.mia_stripe_purchases from anon, authenticated;

create table if not exists public.mia_generation_requests (
  request_id uuid primary key,
  user_id uuid not null references auth.users(id) on delete cascade,
  credit_type text not null check (credit_type in ('free','paid')),
  status text not null default 'reserved' check (status in ('reserved','completed','refunded')),
  created_at timestamptz not null default now()
);
alter table public.mia_generation_requests enable row level security;
revoke all on public.mia_generation_requests from anon, authenticated;

-- The trusted backend will create accounts, reserve credits, and apply verified
-- Stripe webhooks in DB transactions. Do not grant insert/update privileges
-- to authenticated users; service-role credentials must remain server-only.
