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
    username    VARCHAR(20)  NOT NULL UNIQUE,  -- 로그인 아이디, 6~20자 (signup_widget에서 검증)
    password    VARCHAR(255) NOT NULL,         -- ⚠️ 현재 평문으로 저장/비교 중 — 별도 고도화 작업(해싱 적용) 필요
    email       VARCHAR(255) NOT NULL UNIQUE,
    name        VARCHAR(50)  NOT NULL,
    phone       VARCHAR(20)  NOT NULL,         -- '-' 없이 숫자 11자리로 저장
    birthday    DATE,
    member_no   VARCHAR(20)  NOT NULL UNIQUE,  -- 'RUMI-######' 형식 (data/db.py register_user)
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
