-- 카카오 연결 해제 웹훅이 부른다 (supabase/functions/kakao-unlink). 서버 키로만 호출할 수 있다.
create or replace function public.kakao_unlink(kakao_user_id text)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
  owner uuid;
begin
  delete from auth.identities
   where provider = 'kakao' and provider_id = kakao_user_id
  returning user_id into owner;
  if owner is null then
    return;
  end if;
  -- 카카오로만 가입한 계정은 더 들어올 방법이 없으니 로그인 상태를 끊는다.
  -- 계정 자체 삭제는 되돌릴 수 없어 하지 않는다.
  if not exists (select 1 from auth.identities where user_id = owner) then
    delete from auth.sessions where user_id = owner;
  end if;
end;
$$;

revoke all on function public.kakao_unlink(text) from public, anon, authenticated;
grant execute on function public.kakao_unlink(text) to service_role;
