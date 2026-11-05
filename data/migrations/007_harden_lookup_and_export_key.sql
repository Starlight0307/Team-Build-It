-- ============================================================
-- 007_harden_lookup_and_export_key.sql
--
-- 보안 점검(2026-11-03)에서 나온 두 가지를 고친다. Supabase 대시보드
-- → SQL Editor에서 이 파일 전체를 한 번 실행하면 된다(여러 번 실행해도 안전).
--
-- 1) 아이디만 넣으면 그 사람 이메일이 나오던 문제
--    rpc_get_email_by_username / rpc_get_username_by_email 은 로그인하지
--    않은 사람(anon)도 호출할 수 있어서, 앱에 들어 있는 공개 키만 있으면
--    아이디(또는 이메일)를 하나씩 넣어 보며 회원 이메일·아이디를 모을 수 있었다.
--    → 로그인은 "아이디 + 비밀번호가 맞을 때만" 이메일을 돌려주는
--      rpc_login_email 로 바꾸고(틀린 비밀번호 반복은 잠시 막음),
--      아이디 찾기는 이메일 인증 후 받은 본인 세션으로 profiles 를 직접 읽는다
--      (RLS "본인 정보만 조회"). 예전 두 함수는 anon 에서 호출 권한을 뺀다.
--
-- 2) 내보낸 대화/백업 파일(.lumi, .lumibak)의 "계정 키"가 계정 uid 하나로만
--    만들어져 사실상 비밀이 아니던 문제
--    → profiles 에 본인만 읽을 수 있는 무작위 export_secret 을 두고,
--      앱은 이 값을 섞어 키를 만든다(data/chat_crypto.py, LUMIA2).
--
-- [주의] 이 파일을 적용하면 예전 버전 앱(이 수정 이전 코드)은 아이디 로그인이
--        안 된다 — 팀원 모두 최신 코드를 받은 뒤 적용할 것.
-- ============================================================

-- ------------------------------------------------------------
-- 1-a) 로그인 시도 기록 (비밀번호 무차별 대입 방지용)
-- ------------------------------------------------------------
create table if not exists public.login_attempts (
    username      text        not null,
    attempted_at  timestamptz not null default now()
);
create index if not exists login_attempts_username_time
    on public.login_attempts (username, attempted_at);

alter table public.login_attempts enable row level security;
-- 정책을 만들지 않음 → anon/authenticated 는 직접 읽기/쓰기 불가
-- (아래 SECURITY DEFINER 함수만 사용)
revoke all on public.login_attempts from anon, authenticated;

-- ------------------------------------------------------------
-- 1-b) 아이디 + 비밀번호가 맞을 때만 이메일을 돌려주는 함수
--      틀리면 null. 같은 아이디로 15분 안에 10번 넘게 틀리면 잠시 null 만 돌려준다.
--      (실제 로그인 토큰 발급은 여전히 Supabase Auth /auth/v1/token 이 한다)
-- ------------------------------------------------------------
create extension if not exists pgcrypto with schema extensions;

create or replace function public.rpc_login_email(p_username text, p_password text)
returns text
language plpgsql security definer set search_path = public, extensions
as $$
declare
    v_email text;
    v_hash  text;
    v_fails int;
begin
    if p_username is null or p_password is null or length(p_password) = 0 then
        return null;
    end if;

    select count(*) into v_fails
    from public.login_attempts
    where username = p_username
      and attempted_at > now() - interval '15 minutes';
    if v_fails >= 10 then
        return null;
    end if;

    select u.email, u.encrypted_password into v_email, v_hash
    from public.profiles p
    join auth.users u on u.id = p.id
    where p.username = p_username
    limit 1;

    if v_hash is not null and v_hash <> '' and v_hash = crypt(p_password, v_hash) then
        delete from public.login_attempts where username = p_username;
        return v_email;
    end if;

    insert into public.login_attempts (username) values (p_username);
    -- 오래된 기록 정리 (테이블이 계속 커지지 않게)
    delete from public.login_attempts where attempted_at < now() - interval '1 day';
    return null;
end;
$$;

revoke all on function public.rpc_login_email(text, text) from public;
grant execute on function public.rpc_login_email(text, text) to anon, authenticated;

-- ------------------------------------------------------------
-- 1-c) 예전 조회 함수는 더 이상 공개 호출 불가
-- ------------------------------------------------------------
-- (DB에 그 함수가 없으면 건너뛴다 — 없어서 에러가 나 전체가 취소되지 않게)
do $$
begin
    if to_regprocedure('public.rpc_get_email_by_username(text)') is not null then
        revoke execute on function public.rpc_get_email_by_username(text) from public, anon, authenticated;
    end if;
    if to_regprocedure('public.rpc_get_username_by_email(text)') is not null then
        revoke execute on function public.rpc_get_username_by_email(text) from public, anon, authenticated;
    end if;
end;
$$;

-- ------------------------------------------------------------
-- 2) 내보내기 파일용 계정 비밀값 (본인만 읽을 수 있음 — profiles RLS "본인 정보만 조회")
--    기존 회원도 각자 다른 무작위 값이 채워진다(volatile default).
-- ------------------------------------------------------------
alter table public.profiles
    add column if not exists export_secret text not null
    default encode(extensions.gen_random_bytes(32), 'hex');
