-- Apply after schema.sql. Only the server-side service role may execute these.
create or replace function public.acquire_workspace_lease(lease_name text, lease_owner text, ttl_seconds integer)
returns boolean language plpgsql security invoker set search_path = public as $$
declare acquired text;
begin
  insert into job_leases(name,owner,expires_at)
  values(lease_name,lease_owner,clock_timestamp()+make_interval(secs=>least(greatest(ttl_seconds,30),3600)))
  on conflict(name) do update set owner=excluded.owner,expires_at=excluded.expires_at
  where job_leases.expires_at <= clock_timestamp()
  returning owner into acquired;
  return acquired is not null and acquired=lease_owner;
end $$;

create or replace function public.renew_workspace_lease(lease_name text, lease_owner text, ttl_seconds integer)
returns boolean language plpgsql security invoker set search_path = public as $$
begin
  update job_leases set expires_at=clock_timestamp()+make_interval(secs=>least(greatest(ttl_seconds,30),3600))
  where name=lease_name and owner=lease_owner and expires_at>clock_timestamp();
  return found;
end $$;

create or replace function public.save_workspace_snapshot(lease_name text, lease_owner text, state_name text, payload text, byte_count integer)
returns boolean language plpgsql security invoker set search_path = public as $$
begin
  -- Lock the lease row until the write commits: a new owner cannot overtake this write.
  perform 1 from job_leases where name=lease_name and owner=lease_owner
    and expires_at>clock_timestamp() for update;
  if not found then return false; end if;
  insert into cloud_state(name,payload_base64,size_bytes,updated_at)
  values(state_name,payload,byte_count,clock_timestamp())
  on conflict(name) do update set payload_base64=excluded.payload_base64,
    size_bytes=excluded.size_bytes,updated_at=excluded.updated_at;
  return true;
end $$;

revoke all on function public.acquire_workspace_lease(text,text,integer) from public,anon,authenticated;
revoke all on function public.renew_workspace_lease(text,text,integer) from public,anon,authenticated;
revoke all on function public.save_workspace_snapshot(text,text,text,text,integer) from public,anon,authenticated;
grant execute on function public.acquire_workspace_lease(text,text,integer) to service_role;
grant execute on function public.renew_workspace_lease(text,text,integer) to service_role;
grant execute on function public.save_workspace_snapshot(text,text,text,text,integer) to service_role;
