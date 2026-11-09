-- Mia credit operations: run in Supabase SQL Editor after 001_mia_credits.sql.
-- All functions are callable ONLY by the server-side service_role.
-- Never place a service_role key in browser JavaScript.
create or replace function public.mia_get_or_create_credits(p_user_id uuid)
returns table(free_credits integer, paid_credits integer)
language plpgsql security definer set search_path = ''
as $$
begin
  insert into public.mia_credit_accounts(user_id) values(p_user_id)
  on conflict(user_id) do nothing;
  return query select c.free_credits,c.paid_credits
    from public.mia_credit_accounts c where c.user_id=p_user_id;
end; $$;

create or replace function public.mia_reserve_credit(p_user_id uuid, p_request_id uuid)
returns text language plpgsql security definer set search_path = ''
as $$
declare kind text;
begin
  insert into public.mia_credit_accounts(user_id) values(p_user_id)
  on conflict(user_id) do nothing;
  -- The account row is locked by the UPDATE, preventing double-spending.
  update public.mia_credit_accounts
    set free_credits=free_credits-1
    where user_id=p_user_id and free_credits>0;
  if found then
    kind:='free';
  else
    update public.mia_credit_accounts
      set paid_credits=paid_credits-1
      where user_id=p_user_id and paid_credits>0;
    if not found then return 'insufficient'; end if;
    kind:='paid';
  end if;
  insert into public.mia_generation_requests(request_id,user_id,credit_type,status)
    values(p_request_id,p_user_id,kind,'reserved');
  return kind;
end; $$;

create or replace function public.mia_finish_credit(p_user_id uuid, p_request_id uuid, p_success boolean)
returns boolean language plpgsql security definer set search_path = ''
as $$
declare old_kind text;
begin
  update public.mia_generation_requests
    set status=case when p_success then 'completed' else 'refunded' end
    where request_id=p_request_id and user_id=p_user_id and status='reserved'
    returning credit_type into old_kind;
  if old_kind is null then return false; end if;
  if not p_success then
    if old_kind='free' then
      update public.mia_credit_accounts set free_credits=free_credits+1 where user_id=p_user_id;
    else
      update public.mia_credit_accounts set paid_credits=paid_credits+1 where user_id=p_user_id;
    end if;
  end if;
  return true;
end; $$;

create or replace function public.mia_grant_purchase(p_user_id uuid, p_session_id text, p_event_id text)
returns boolean language plpgsql security definer set search_path = ''
as $$
begin
  insert into public.mia_credit_accounts(user_id) values(p_user_id)
  on conflict(user_id) do nothing;
  insert into public.mia_stripe_purchases(session_id,user_id,stripe_event_id,credits)
    values(p_session_id,p_user_id,p_event_id,3)
  on conflict do nothing;
  if not found then return false; end if;
  update public.mia_credit_accounts set paid_credits=paid_credits+3 where user_id=p_user_id;
  return true;
end; $$;

revoke all on function public.mia_get_or_create_credits(uuid) from public,anon,authenticated;
revoke all on function public.mia_reserve_credit(uuid,uuid) from public,anon,authenticated;
revoke all on function public.mia_finish_credit(uuid,uuid,boolean) from public,anon,authenticated;
revoke all on function public.mia_grant_purchase(uuid,text,text) from public,anon,authenticated;
grant execute on function public.mia_get_or_create_credits(uuid) to service_role;
grant execute on function public.mia_reserve_credit(uuid,uuid) to service_role;
grant execute on function public.mia_finish_credit(uuid,uuid,boolean) to service_role;
grant execute on function public.mia_grant_purchase(uuid,text,text) to service_role;
