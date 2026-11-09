-- Project Iblis API: D1 schema. Apply with: wrangler d1 execute iblis --file worker/schema.sql (add --local for dev)

CREATE TABLE IF NOT EXISTS users (
  id            INTEGER PRIMARY KEY,          -- GitHub user id
  login         TEXT NOT NULL,                -- GitHub login
  show_name     INTEGER NOT NULL DEFAULT 0,   -- 0 = anonymous on the board (default), 1 = show login
  terms_utc     TEXT,                         -- when the contributor terms were accepted (at approval)
  paused_until  TEXT,                         -- set by too many expired leases or strikes
  created_utc   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

-- Private keys an agent uses to act as the contributor. Only a SHA-256 is stored.
CREATE TABLE IF NOT EXISTS keys (
  hash        TEXT PRIMARY KEY,
  user_id     INTEGER NOT NULL REFERENCES users(id),
  label       TEXT,
  created_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  revoked_utc TEXT
);

-- The function queue, mirrored from the code repository by the build host (POST /api/v1/host/sync).
-- state: open | matched | excluded (library, not a function, held by a ruling)
CREATE TABLE IF NOT EXISTS functions (
  va            TEXT PRIMARY KEY,             -- '0x00401220'
  length        INTEGER NOT NULL,
  module        TEXT NOT NULL,
  state         TEXT NOT NULL,
  name          TEXT,                         -- current best name, if any
  flags         TEXT,                         -- compile flags for this function (profile + rulings)
  reference     TEXT,                         -- reference-source pointer, words only (not sent at present)
  body_sha256   TEXT,                         -- SHA-256 of the original bytes, so the checker can confirm the player's copy
  tries         INTEGER NOT NULL DEFAULT 0,   -- leases handed out
  best_source   TEXT,                         -- closest code from a give-up, for the next agent
  best_note     TEXT                          -- what was tried, in words
);
CREATE INDEX IF NOT EXISTS functions_band ON functions(state, length);

-- A lease is the silent 2-hour hold behind `next`: it keeps two agents off one function. Contributors never manage it.
CREATE TABLE IF NOT EXISTS leases (
  va           TEXT PRIMARY KEY REFERENCES functions(va),
  user_id      INTEGER NOT NULL REFERENCES users(id),
  leased_utc   TEXT NOT NULL,
  expires_utc  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS leases_user ON leases(user_id);

-- Leases that ran out without submit or giveup (counted for the pause rule).
CREATE TABLE IF NOT EXISTS expired (
  va          TEXT NOT NULL,
  user_id     INTEGER NOT NULL,
  expired_utc TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS expired_user ON expired(user_id, expired_utc);

-- Every finished lease: a submitted match or a give-up.
-- status: pending -> verified | failed | review (host disagrees with the local check: not a strike) ; giveup
CREATE TABLE IF NOT EXISTS results (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  va            TEXT NOT NULL REFERENCES functions(va),
  user_id       INTEGER NOT NULL REFERENCES users(id),
  kind          TEXT NOT NULL,                -- 'match' | 'giveup'
  model         TEXT,                         -- the agent's model, from its work notes
  source        TEXT,                         -- candidate.cpp (closest code for a give-up)
  notes         TEXT,                         -- work notes / give-up reason
  local_status  TEXT,                         -- the local checker's verdict (RAW_EQUAL, RELOCATION_RESOLVED_EQUAL)
  body_sha256   TEXT,
  status        TEXT NOT NULL,
  host_note     TEXT,
  created_utc   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  decided_utc   TEXT
);
CREATE INDEX IF NOT EXISTS results_status ON results(status, id);
CREATE INDEX IF NOT EXISTS results_user ON results(user_id, status);

-- Device sign-in: the agent shows a link and a short code; the contributor approves it with GitHub.
CREATE TABLE IF NOT EXISTS approvals (
  device_code  TEXT PRIMARY KEY,              -- secret the agent polls with
  user_code    TEXT NOT NULL UNIQUE,          -- short code the contributor types on the site
  user_id      INTEGER,                       -- set once approved
  key_plain    TEXT,                          -- handed to the agent once, then cleared
  created_utc  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE IF NOT EXISTS oauth_states (
  state       TEXT PRIMARY KEY,
  user_code   TEXT,
  created_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
