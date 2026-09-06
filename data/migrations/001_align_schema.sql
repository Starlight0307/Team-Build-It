-- ============================================================
-- 001_align_schema.sql
--
-- 이미 데이터가 들어있는 운영 Supabase DB의 users 테이블을
-- data/schema.sql이 문서화한 최신 스키마와 맞추는 마이그레이션입니다.
-- 전부 IF NOT EXISTS / 존재 여부 체크로 감싸져 있어 이미 적용된
-- 항목이 있어도 안전하게 반복 실행할 수 있습니다.
--
-- 캘린더(calendar_events) 관련 스키마는 다른 팀원이 담당 중이라
-- 이 마이그레이션에서는 다루지 않습니다.
--
-- 실행: Supabase SQL Editor 또는 psql로 이 파일 내용을 그대로 실행하세요.
-- ============================================================

-- 1) users: 감사(audit)용 생성 시각 컬럼 추가
ALTER TABLE users ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

-- 2) users: 중복 방지 제약조건 보정 (없으면 추가)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'users_username_key'
    ) THEN
        ALTER TABLE users ADD CONSTRAINT users_username_key UNIQUE (username);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'users_email_key'
    ) THEN
        ALTER TABLE users ADD CONSTRAINT users_email_key UNIQUE (email);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'users_member_no_key'
    ) THEN
        ALTER TABLE users ADD CONSTRAINT users_member_no_key UNIQUE (member_no);
    END IF;
END $$;
