CREATE TABLE IF NOT EXISTS calendar_events (
    event_id VARCHAR(255) PRIMARY KEY,       -- 구글 캘린더 이벤트 고유 ID
    user_id INT,
    summary TEXT,                            -- 일정 제목
    start_datetime DATETIME,
    end_datetime DATETIME,
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
);