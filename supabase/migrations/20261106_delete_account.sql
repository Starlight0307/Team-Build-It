-- 회원 탈퇴 (supabase/functions/delete-account). 서버 키로만 호출할 수 있다.
create or replace function public.delete_user_account(target uuid)
returns void
language plpgsql
security definer
set search_path = ''
as $$
begin
  delete from public.login_events where user_id = target;
  delete from public.profiles where id = target;
  delete from auth.users where id = target;   -- 연결된 로그인 수단과 세션도 함께 지워진다
end;
$$;

revoke all on function public.delete_user_account(uuid) from public, anon, authenticated;
grant execute on function public.delete_user_account(uuid) to service_role;
