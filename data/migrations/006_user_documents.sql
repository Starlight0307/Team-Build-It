-- 006_user_documents.sql
-- 계정별 개인 데이터(설정, 루미 기억, 할 일, 메모, 가계부, 알림 …)를 서버에 동기화하기 위한 테이블.
--
-- 구조: 문서(document) 하나 = 한 계정의 한 종류(collection)의 한 파일/항목(doc_key).
--   collection: 플러그인/기능 이름 (예: 'todo_list', 'settings')
--   doc_key   : 그 안의 항목 이름 (예: 'todo_list', 'budget')
--   data      : 내용 (JSON)
-- 플러그인이 늘어도 테이블을 바꿀 필요가 없다. 가계부/캘린더처럼 무거워지면 전용 테이블로 옮긴다.
--
-- 보안: RLS로 로그인한 본인(auth.uid()) 행만 읽고 쓸 수 있다. 비로그인(anon)은 접근 불가.
-- 대화기록(chat_logs)은 이 테이블에 올리지 않는다(로컬 암호화 저장 유지). IoT 씬/방, 시스템 기록도 제외.
--
-- 실행: Supabase 대시보드 → SQL Editor 에 이 파일 내용을 붙여넣고 Run. (여러 번 실행해도 안전)

create table if not exists public.user_documents (
    user_id     uuid        not null default auth.uid() references auth.users (id) on delete cascade,
    collection  text        not null check (char_length(collection) between 1 and 64),
    doc_key     text        not null check (char_length(doc_key) between 1 and 200),
    data        jsonb       not null default '{}'::jsonb check (pg_column_size(data) < 1048576),  -- 문서당 1MB 이하
    updated_at  timestamptz not null default now(),
    primary key (user_id, collection, doc_key)
);

create index if not exists user_documents_user_updated_idx
    on public.user_documents (user_id, updated_at desc);

-- 수정 시각은 항상 서버 시각으로 (클라이언트 시계가 틀려도 충돌 판단이 어긋나지 않게)
create or replace function public.user_documents_touch()
returns trigger
language plpgsql
as $$
begin
    new.updated_at := now();
    return new;
end;
$$;

drop trigger if exists user_documents_touch_trg on public.user_documents;
create trigger user_documents_touch_trg
    before insert or update on public.user_documents
    for each row execute function public.user_documents_touch();

alter table public.user_documents enable row level security;

drop policy if exists "user_documents_select_own" on public.user_documents;
create policy "user_documents_select_own" on public.user_documents
    for select to authenticated using (auth.uid() = user_id);

drop policy if exists "user_documents_insert_own" on public.user_documents;
create policy "user_documents_insert_own" on public.user_documents
    for insert to authenticated with check (auth.uid() = user_id);

drop policy if exists "user_documents_update_own" on public.user_documents;
create policy "user_documents_update_own" on public.user_documents
    for update to authenticated using (auth.uid() = user_id) with check (auth.uid() = user_id);

drop policy if exists "user_documents_delete_own" on public.user_documents;
create policy "user_documents_delete_own" on public.user_documents
    for delete to authenticated using (auth.uid() = user_id);

revoke all on public.user_documents from anon;
grant select, insert, update, delete on public.user_documents to authenticated;
