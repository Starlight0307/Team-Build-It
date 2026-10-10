-- 006_login_events.sql
-- 로그인 기록을 서버(Supabase)에 남긴다. 서버에 저장하는 개인 정보는 이것 하나뿐이다 —
-- 설정, 대화, 할 일, 메모 등 다른 개인 기록은 모두 사용자 PC에만 저장한다.
--
-- 저장 내용: 계정(auth.uid), 로그인 방식(비밀번호/Google/자동 로그인), 기기 종류(예: Windows), 시각.
-- IP 주소나 아이디/비밀번호는 저장하지 않는다.
--
-- 보안: RLS로 본인 행만 읽고 추가할 수 있다. 수정/삭제 정책이 없으므로 앱에서는 기록을 고치거나
-- 지울 수 없다(감사용). 계정이 삭제되면 같이 지워진다(on delete cascade).
--
-- 실행: Supabase 대시보드 → SQL Editor 에 붙여넣고 Run. (여러 번 실행해도 안전)

create table if not exists public.login_events (
    id            bigint generated always as identity primary key,
    user_id       uuid        not null default auth.uid() references auth.users (id) on delete cascade,
    method        text        not null check (char_length(method) between 1 and 40),
    device        text        check (char_length(device) <= 80),
    logged_in_at  timestamptz not null default now()
);

create index if not exists login_events_user_time_idx
    on public.login_events (user_id, logged_in_at desc);

-- 시각은 항상 서버 시각으로 (클라이언트가 시각을 조작하지 못하게)
create or replace function public.login_events_touch()
returns trigger
language plpgsql
as $$
begin
    new.logged_in_at := now();
    return new;
end;
$$;

drop trigger if exists login_events_touch_trg on public.login_events;
create trigger login_events_touch_trg
    before insert on public.login_events
    for each row execute function public.login_events_touch();

alter table public.login_events enable row level security;

drop policy if exists "login_events_select_own" on public.login_events;
create policy "login_events_select_own" on public.login_events
    for select to authenticated using (auth.uid() = user_id);

drop policy if exists "login_events_insert_own" on public.login_events;
create policy "login_events_insert_own" on public.login_events
    for insert to authenticated with check (auth.uid() = user_id);

revoke all on public.login_events from anon;
grant select, insert on public.login_events to authenticated;
