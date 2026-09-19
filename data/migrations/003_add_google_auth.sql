-- ============================================================
-- 003_add_google_auth.sql
--
-- 구글 로그인 기능 추가.
-- - users에 google_id 컬럼 추가 (구글 계정 고유 ID, sub)
-- - 구글 전용 가입 계정은 비밀번호/휴대폰번호를 안 받으므로 해당
--   컬럼의 NOT NULL 제약을 완화
--
-- 전부 IF NOT EXISTS / 존재 여부 체크로 감싸져 있어 반복 실행해도
-- 안전합니다.
-- ============================================================

ALTER TABLE users ADD COLUMN IF NOT EXISTS google_id VARCHAR(255);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'users_google_id_key'
    ) THEN
        ALTER TABLE users ADD CONSTRAINT users_google_id_key UNIQUE (google_id);
    END IF;
END $$;

ALTER TABLE users ALTER COLUMN password DROP NOT NULL;
ALTER TABLE users ALTER COLUMN phone DROP NOT NULL;
