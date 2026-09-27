-- ============================================================
-- 004_supabase_auth_schema.sql
--
-- 로그인/회원가입을 "우리가 직접 psycopg2로 접속하는 방식"에서
-- "Supabase 공식 Auth 시스템(auth.users)" 방식으로 전환하기 위한
-- 스키마 작업. 이렇게 바꾸는 이유:
--
--   지금까지는 배포된 앱 안에 DB 마스터 비밀번호(SUPABASE_PASSWORD)가
--   그대로 들어있어야 했음 — 이 비밀번호만 빼내면 회원 전체 데이터를
--   전부 읽고 쓰고 지울 수 있는 구조라, 팀플 시연 단계는 몰라도
--   실제로 배포하면 심각한 보안 사고임.
--
--   Supabase Auth + 아래 RLS(행 수준 보안) 구조로 바꾸면, 앱에는
--   "공개돼도 안전한 키"(anon key)만 들어가고, 각자 자기 자신의
--   데이터만 보고 고칠 수 있게 서버가 걸러준다.
--
-- 구성:
--   1) public.profiles — auth.users를 보완하는 추가 정보 테이블
--      (username, phone, birthday, member_no, google_id)
--   2) 트리거 — auth.users에 새 사용자가 생기면 profiles를 자동 생성
--      (로컬 가입/구글 가입 모두 동일하게 처리)
--   3) RPC 함수 — 아이디 중복확인, 이메일→아이디, 아이디→이메일 조회처럼
--      비로그인 상태에서도 필요한 "안전하게 제한된" 조회를 위한 함수
-- ============================================================

-- ------------------------------------------------------------
-- 1) profiles 테이블
-- ------------------------------------------------------------
create table if not exists public.profiles (
    id          uuid primary key references auth.users(id) on delete cascade,
    username    varchar(20)  not null unique,
    name        varchar(50),
    phone       varchar(20),
    birthday    date,
    member_no   varchar(20)  not null unique,
    google_id   text unique,
    created_at  timestamptz  not null default now()
);

alter table public.profiles enable row level security;

drop policy if exists "본인 정보만 조회" on public.profiles;
create policy "본인 정보만 조회" on public.profiles
    for select using (auth.uid() = id);

drop policy if exists "본인 정보만 수정" on public.profiles;
create policy "본인 정보만 수정" on public.profiles
    for update using (auth.uid() = id);

-- insert는 아래 트리거(SECURITY DEFINER)만 수행 — 일반 권한으로는
-- profiles에 직접 행을 추가할 수 없음(정책을 안 만들면 기본 차단).

-- ------------------------------------------------------------
-- 2) 신규 가입 시 profiles 자동 생성 트리거
--
-- 로컬 가입(아이디/비번)은 signup 호출 시 넘긴 raw_user_meta_data에
-- username/name/phone/birthday가 들어있고, 구글 가입은 그게 없는
-- 대신 raw_user_meta_data에 구글이 채워주는 sub/full_name/email이
-- 있다. 두 경우를 구분해서 처리한다.
-- ------------------------------------------------------------
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer set search_path = public
as $$
declare
    v_username  text;
    v_member_no text;
    v_google_id text;
    v_name      text;
    v_phone     text;
    v_birthday  date;
    v_suffix    text;
begin
    v_google_id := new.raw_user_meta_data->>'sub';
    v_username  := new.raw_user_meta_data->>'username';

    if v_username is not null and v_username <> '' then
        -- 로컬 가입: 앱에서 넘긴 값 그대로 사용
        v_name     := new.raw_user_meta_data->>'name';
        v_phone    := new.raw_user_meta_data->>'phone';
        v_birthday := nullif(new.raw_user_meta_data->>'birthday', '')::date;

        v_suffix    := lpad(floor(random() * 1000000)::int::text, 6, '0');
        v_member_no := 'RUMI-' || v_suffix;
        while exists (select 1 from public.profiles where member_no = v_member_no) loop
            v_suffix    := lpad(floor(random() * 1000000)::int::text, 6, '0');
            v_member_no := 'RUMI-' || v_suffix;
        end loop;
    else
        -- 구글 가입: 아이디를 이메일 앞부분에서 유도, 중복이면 숫자를 붙임
        v_username := regexp_replace(split_part(coalesce(new.email, ''), '@', 1), '[^a-zA-Z0-9]', '', 'g');
        if v_username is null or v_username = '' then
            v_username := 'google' || substr(coalesce(v_google_id, new.id::text), 1, 6);
        end if;
        v_username := left(v_username, 14);

        while exists (select 1 from public.profiles where username = v_username) loop
            v_username := left(v_username, 10) || floor(random() * 9000 + 1000)::text;
        end loop;

        v_name     := coalesce(new.raw_user_meta_data->>'full_name', new.raw_user_meta_data->>'name', v_username);
        v_phone    := null;
        v_birthday := null;
        v_member_no := 'RUMI-G-' || substr(coalesce(v_google_id, new.id::text), greatest(length(coalesce(v_google_id, new.id::text)) - 7, 1));

        -- 극히 드물게 겹치면 랜덤 숫자로 대체
        if exists (select 1 from public.profiles where member_no = v_member_no) then
            v_member_no := 'RUMI-G-' || lpad(floor(random() * 100000000)::int::text, 8, '0');
        end if;
    end if;

    insert into public.profiles (id, username, name, phone, birthday, member_no, google_id)
    values (new.id, v_username, v_name, v_phone, v_birthday, v_member_no, v_google_id)
    on conflict (id) do nothing;

    return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
    after insert on auth.users
    for each row execute function public.handle_new_user();

-- ------------------------------------------------------------
-- 3) 비로그인 상태에서도 필요한 조회용 RPC 함수
--
-- 전부 SECURITY DEFINER로, 딱 필요한 값 하나만 반환하도록 좁게 만듦
-- (예: 비밀번호 해시 같은 민감정보는 노출 안 함).
-- ------------------------------------------------------------
create or replace function public.rpc_get_email_by_username(p_username text)
returns text
language sql security definer set search_path = public stable
as $$
    select u.email
    from public.profiles p
    join auth.users u on u.id = p.id
    where p.username = p_username
    limit 1;
$$;

create or replace function public.rpc_get_username_by_email(p_email text)
returns text
language sql security definer set search_path = public stable
as $$
    select p.username
    from public.profiles p
    join auth.users u on u.id = p.id
    where u.email = p_email
    limit 1;
$$;

create or replace function public.rpc_username_exists(p_username text)
returns boolean
language sql security definer set search_path = public stable
as $$
    select exists(select 1 from public.profiles where username = p_username);
$$;

create or replace function public.rpc_email_exists(p_email text)
returns boolean
language sql security definer set search_path = public stable
as $$
    select exists(select 1 from auth.users where email = p_email);
$$;

grant execute on function public.rpc_get_email_by_username(text) to anon, authenticated;
grant execute on function public.rpc_get_username_by_email(text) to anon, authenticated;
grant execute on function public.rpc_username_exists(text) to anon, authenticated;
grant execute on function public.rpc_email_exists(text) to anon, authenticated;
