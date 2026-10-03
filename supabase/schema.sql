-- Run this migration in the Supabase SQL editor before enabling cloud jobs.
-- The service-role key is server-only and must never be exposed to Streamlit clients.

create table if not exists public.campaign_jobs (
  id text primary key,
  name text not null,
  settings jsonb not null,
  status text not null default 'ready',
  reason text not null default '',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  heartbeat double precision not null default 0,
  owner text not null default '',
  stop_requested boolean not null default false,
  search_calls integer not null default 0,
  ai_calls integer not null default 0,
  duplicates integer not null default 0,
  source_errors integer not null default 0
);

create table if not exists public.campaign_queries (
  id bigint generated always as identity primary key,
  job_id text not null references public.campaign_jobs(id) on delete cascade,
  data jsonb not null,
  source text not null default '',
  page integer not null default 1,
  status text not null default 'pending',
  attempts integer not null default 0
);

create index if not exists campaign_queries_queue
  on public.campaign_queries (job_id, status, page, id);

create table if not exists public.campaign_candidates (
  id bigint generated always as identity primary key,
  job_id text not null references public.campaign_jobs(id) on delete cascade,
  domain text not null,
  data jsonb not null,
  status text not null default 'pending',
  unique (job_id, domain)
);

create table if not exists public.leads (
  domain text primary key,
  job_id text not null references public.campaign_jobs(id) on delete cascade,
  email text not null default '',
  qualification_status text not null,
  data jsonb not null,
  created_at timestamptz not null default now()
);

create index if not exists leads_email_idx on public.leads (email);
create index if not exists leads_job_status_idx on public.leads (job_id, qualification_status);

create table if not exists public.outreach_settings (
  id boolean primary key default true check (id),
  preferences jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now()
);

create table if not exists public.outreach_deliveries (
  id text primary key,
  sender text not null,
  sheet_id text not null,
  domain text not null,
  email text not null,
  stage integer not null,
  message_id text not null unique,
  subject text not null,
  body text not null,
  html text not null,
  reply_to text not null default '',
  lead jsonb not null,
  state text not null,
  created_at timestamptz not null default now(),
  sent_at timestamptz,
  local_day date not null,
  reason text not null default '',
  sheet_synced boolean not null default false,
  unique (sender, domain, stage)
);

create index if not exists outreach_daily_usage
  on public.outreach_deliveries (sender, local_day, state);

create table if not exists public.outreach_stops (
  identity text primary key,
  status text not null,
  reason text not null,
  updated_at timestamptz not null default now()
);

create table if not exists public.job_leases (
  name text primary key,
  owner text not null,
  expires_at timestamptz not null
);

create table if not exists public.cloud_state (
  name text primary key,
  payload_base64 text not null,
  size_bytes integer not null default 0,
  updated_at timestamptz not null default now()
);

alter table public.campaign_jobs enable row level security;
alter table public.campaign_queries enable row level security;
alter table public.campaign_candidates enable row level security;
alter table public.leads enable row level security;
alter table public.outreach_settings enable row level security;
alter table public.outreach_deliveries enable row level security;
alter table public.outreach_stops enable row level security;
alter table public.job_leases enable row level security;
alter table public.cloud_state enable row level security;

-- No public or anon policies are created. Server jobs use the service-role key.
