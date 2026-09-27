-- ============================================================
-- 005_migrate_existing_users.sql
--
-- 기존 public.users(커스텀 테이블)에 있던 회원 7명을 auth.users +
-- public.profiles로 옮긴다. 비밀번호는 이미 bcrypt로 해시돼 있던
-- 것(예전에 로그인 성공 시 자동 승격된 계정들)은 그 해시를 그대로
-- 복사하고, 아직 평문인 계정은 pgcrypto로 그 자리에서 해싱한다 —
-- 어느 쪽이든 회원 본인이 비밀번호를 다시 설정할 필요는 없다.
--
-- auth.users에 넣으면 004번 트리거가 자동으로 profiles 행도 만드는데,
-- 트리거는 "새로 가입하는 사람"을 가정해서 member_no 등을 새로
-- 생성하므로, 이 마이그레이션에서는 트리거가 만든 값 위에 기존
-- 실제 값(member_no, google_id 등)을 덮어써서 정확히 보존한다.
--
-- 이 스크립트는 public.users에 있던 특정 이메일들을 대상으로 하므로
-- 반복 실행해도 이미 옮겨진 이메일은 건너뛴다(NOT EXISTS 체크).
-- ============================================================

create extension if not exists pgcrypto;

do $$
declare
    r record;
    v_new_id uuid;
    v_encrypted text;
    v_app_meta jsonb;
begin
    for r in select * from public.users loop
        -- 이미 이전된 이메일이면 건너뜀
        if exists (select 1 from auth.users where email = r.email) then
            continue;
        end if;

        if r.password is null then
            v_encrypted := null;  -- 구글 전용 계정(비밀번호 없음)
            v_app_meta := '{"provider":"google","providers":["google"]}'::jsonb;
        elsif r.password like '$2%' then
            v_encrypted := r.password;  -- 이미 bcrypt 해시 — 그대로 복사
            v_app_meta := '{"provider":"email","providers":["email"]}'::jsonb;
        else
            v_encrypted := crypt(r.password, gen_salt('bf'));  -- 평문 → 그 자리에서 해싱
            v_app_meta := '{"provider":"email","providers":["email"]}'::jsonb;
        end if;

        v_new_id := gen_random_uuid();

        insert into auth.users (
            instance_id, id, aud, role, email, encrypted_password,
            email_confirmed_at, raw_app_meta_data, raw_user_meta_data,
            created_at, updated_at,
            confirmation_token, recovery_token, email_change_token_new, email_change
        ) values (
            '00000000-0000-0000-0000-000000000000',
            v_new_id, 'authenticated', 'authenticated', r.email, v_encrypted,
            now(), v_app_meta,
            jsonb_build_object('username', r.username, 'name', r.name),
            coalesce(r.created_at, now()), now(),
            '', '', '', ''
        );

        -- 004번 트리거가 이 시점에 profiles 행을 자동 생성함(member_no 등은
        -- 새로 생성된 임시값) — 아래에서 원래 값으로 정확히 덮어씀.
        update public.profiles
        set phone     = r.phone,
            birthday  = r.birthday,
            member_no = r.member_no,
            google_id = r.google_id,
            created_at = coalesce(r.created_at, created_at)
        where id = v_new_id;
    end loop;
end $$;

-- 결과 확인용
select u.email, p.username, p.member_no, p.google_id, (u.encrypted_password is not null) as has_password
from public.profiles p
join auth.users u on u.id = p.id
order by p.created_at;
