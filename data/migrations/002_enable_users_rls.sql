-- ============================================================
-- 002_enable_users_rls.sql
--
-- users 테이블에 RLS(행 수준 보안)를 켭니다.
--
-- 이 앱은 Supabase 클라이언트(anon/authenticated 키)가 아니라
-- psycopg2로 DB에 직접 접속해서 씁니다(.env의 SUPABASE_USER 등).
-- 이 접속 계정은 보통 postgres 롤(BYPASSRLS 권한)이라 RLS를 켜도
-- 지금 앱의 로그인/회원가입/비번찾기 기능에는 영향이 없습니다.
--
-- 목적은 혹시 나중에 누군가 실수로 anon/authenticated 키로 이
-- 테이블에 접근하는 코드를 추가하더라도, 별도 정책(policy)을 만들기
-- 전까지는 기본적으로 전부 차단되도록 안전장치를 걸어두는 것입니다.
--
-- 실행 후 반드시 로그인/회원가입이 정상 동작하는지 확인하세요.
-- ============================================================

ALTER TABLE users ENABLE ROW LEVEL SECURITY;

-- 정책(policy)을 하나도 안 만들면 anon/authenticated 롤은 전부 접근이
-- 막힙니다 (postgres 롤 등 BYPASSRLS 권한을 가진 접속만 계속 동작).
-- 나중에 Supabase 클라이언트로 users를 직접 조회해야 하는 기능이
-- 생기면, 그때 필요한 범위만 허용하는 정책을 추가하세요. 예:
--
-- CREATE POLICY "본인 정보만 조회" ON users
--   FOR SELECT USING (auth.uid()::text = id::text);
