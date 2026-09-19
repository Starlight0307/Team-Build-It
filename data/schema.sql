-- ============================================================
-- Team-Build-It DB 스키마 (Supabase Postgres)
--
-- users 섹션은 코드(data/db.py, widget/login_widget.py,
-- widget/signup_widget.py)에 흩어져 있던 쿼리들을 근거로 실제
-- 운영 중인 스키마를 문서화하고, 새 Supabase 프로젝트를 셋업할 때
-- 그대로 실행할 수 있게 정리한 것입니다. 이미 데이터가 있는
-- 기존(운영) DB에는 이 파일을 그대로 실행하지 말고
-- data/migrations/001_align_schema.sql 을 사용하세요.
--
-- calendar_events는 다른 팀원이 담당 중인 영역이라 원본 그대로
-- 유지합니다.
-- ============================================================

CREATE TABLE IF NOT EXISTS calendar_events (
    event_id VARCHAR(255) PRIMARY KEY,       -- 구글 캘린더 이벤트 고유 ID
    user_id INT,
    summary TEXT,                            -- 일정 제목
    start_datetime DATETIME,
    end_datetime DATETIME,
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
);

-- ------------------------------------------------------------
-- users : 회원 계정
-- 사용처: widget/login_widget.py, widget/signup_widget.py, data/db.py
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    id          SERIAL PRIMARY KEY,
    username    VARCHAR(20)  NOT NULL UNIQUE,  -- 로그인 아이디, 6~20자 (signup_widget에서 검증).
                                                -- 구글 가입은 이메일 앞부분에서 자동 생성 (find_or_create_google_user)
    password    VARCHAR(255),                  -- bcrypt 해시 저장 (data/db.py _hash_password).
                                                -- 기존 평문 계정은 로그인 성공 시 자동으로 해시로 승격됨 (verify_login).
                                                -- 구글 전용 계정은 비밀번호가 없어 NULL
    email       VARCHAR(255) NOT NULL UNIQUE,
    name        VARCHAR(50)  NOT NULL,
    phone       VARCHAR(20),                   -- '-' 없이 숫자 11자리로 저장. 구글 가입은 수집하지 않아 NULL
    birthday    DATE,
    member_no   VARCHAR(20)  NOT NULL UNIQUE,  -- 로컬 가입: 'RUMI-######', 구글 가입: 'RUMI-G-########'
                                                -- (data/db.py register_user / find_or_create_google_user)
    google_id   VARCHAR(255) UNIQUE,           -- 구글 계정 고유 ID(sub). 로컬 가입 계정은 NULL
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
