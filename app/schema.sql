-- 음료조사 v1 스키마 (SQLite)
-- 시간 문자열 규약: 날짜 'YYYY-MM-DD', 시각 'HH:MM', 일시 'YYYY-MM-DD HH:MM[:SS]' (로컬 시간)

CREATE TABLE IF NOT EXISTS users (
  id          INTEGER PRIMARY KEY,
  google_sub  TEXT UNIQUE,                -- 최초 로그인 때 바인딩, 사전 등록 시 NULL
  email       TEXT NOT NULL UNIQUE,       -- admin이 등록하는 회사 이메일 (바인딩 매칭 키)
  name        TEXT NOT NULL,
  role        TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('admin','member')),
  status      TEXT NOT NULL DEFAULT 'invited' CHECK (status IN ('invited','active')),
  created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS cafes (
  id              INTEGER PRIMARY KEY,
  name            TEXT NOT NULL,
  menu_url        TEXT,
  default_menu_id INTEGER REFERENCES menus(id),   -- 공통 기본음료
  is_active       INTEGER NOT NULL DEFAULT 1,
  created_by      INTEGER NOT NULL REFERENCES users(id),
  created_at      TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS menus (
  id          INTEGER PRIMARY KEY,
  cafe_id     INTEGER NOT NULL REFERENCES cafes(id),
  name        TEXT NOT NULL,
  base_price  INTEGER NOT NULL,
  options     TEXT NOT NULL DEFAULT '[]',  -- JSON: [{name, required, choices:[{label, delta}]}]
  is_active   INTEGER NOT NULL DEFAULT 1,
  created_by  INTEGER NOT NULL REFERENCES users(id),
  created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  updated_by  INTEGER REFERENCES users(id),  -- 아무나 수정 가능하므로 마지막 수정자 기록
  updated_at  TEXT
);

CREATE TABLE IF NOT EXISTS groups (
  id              INTEGER PRIMARY KEY,
  name            TEXT NOT NULL UNIQUE,
  parent_group_id INTEGER REFERENCES groups(id),  -- 트리: NULL이면 최상위(본부)
  created_at      TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS group_members (
  group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
  user_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  PRIMARY KEY (group_id, user_id)
);

CREATE TABLE IF NOT EXISTS user_cafe_defaults (
  user_id          INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  cafe_id          INTEGER NOT NULL REFERENCES cafes(id) ON DELETE CASCADE,
  menu_id          INTEGER NOT NULL REFERENCES menus(id),
  selected_options TEXT NOT NULL DEFAULT '[]',   -- [{name, choice, delta}]
  PRIMARY KEY (user_id, cafe_id)
);

CREATE TABLE IF NOT EXISTS survey_schedules (
  id            INTEGER PRIMARY KEY,
  group_id      INTEGER NOT NULL REFERENCES groups(id),
  cafe_id       INTEGER NOT NULL REFERENCES cafes(id),
  weekday       INTEGER NOT NULL CHECK (weekday BETWEEN 0 AND 6),  -- 0=월 … 6=일
  deadline_time TEXT NOT NULL,                                     -- 'HH:MM' (조사 당일 마감 시각)
  allow_guests  INTEGER NOT NULL DEFAULT 0,
  title_pattern TEXT,                                              -- 예: '{M/D} 주간회의'
  enabled       INTEGER NOT NULL DEFAULT 1,
  created_by    INTEGER NOT NULL REFERENCES users(id),
  created_at    TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS surveys (
  id           INTEGER PRIMARY KEY,
  title        TEXT,
  survey_date  TEXT NOT NULL,
  group_id     INTEGER NOT NULL REFERENCES groups(id),
  cafe_id      INTEGER NOT NULL REFERENCES cafes(id),
  deadline_at  TEXT NOT NULL,
  allow_guests INTEGER NOT NULL DEFAULT 0,
  schedule_id  INTEGER REFERENCES survey_schedules(id),
  status       TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','closed')),
  created_by   INTEGER NOT NULL REFERENCES users(id),
  created_at   TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
-- 자동 생성 멱등성: 같은 스케줄이 같은 날짜에 두 번 조사를 만들지 않는다
CREATE UNIQUE INDEX IF NOT EXISTS uq_surveys_schedule_date
  ON surveys(schedule_id, survey_date) WHERE schedule_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS survey_responses (
  id                  INTEGER PRIMARY KEY,
  survey_id           INTEGER NOT NULL REFERENCES surveys(id) ON DELETE CASCADE,
  participant_user_id INTEGER REFERENCES users(id),  -- 잔의 주인. 게스트 잔이면 NULL
  guest_label         TEXT,                          -- 게스트 표시명
  menu_id             INTEGER NOT NULL REFERENCES menus(id),
  selected_options    TEXT NOT NULL DEFAULT '[]',    -- 선택 시점 스냅샷
  final_price         INTEGER NOT NULL,              -- 가격 스냅샷
  is_auto             INTEGER NOT NULL DEFAULT 0,    -- 마감 시 자동 채택
  created_by          INTEGER NOT NULL REFERENCES users(id),  -- 게스트 잔 추가자 표시용
  created_at          TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  updated_at          TEXT,
  CHECK (participant_user_id IS NOT NULL OR guest_label IS NOT NULL)
);
-- 1인 1잔 (본인 잔만, 게스트 잔은 제한 없음)
CREATE UNIQUE INDEX IF NOT EXISTS uq_responses_participant
  ON survey_responses(survey_id, participant_user_id) WHERE participant_user_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_responses_survey ON survey_responses(survey_id);
CREATE INDEX IF NOT EXISTS ix_menus_cafe ON menus(cafe_id);
CREATE INDEX IF NOT EXISTS ix_surveys_group ON surveys(group_id);
