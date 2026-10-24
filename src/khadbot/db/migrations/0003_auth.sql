-- 0003_auth.sql
-- Sign-in with Warcraft Logs (src/khadbot/auth/): users, browser sessions, sign-ins in progress, encrypted WCL
-- tokens and cached report-access answers. Nothing secret is stored in the clear: session IDs and OAuth states are
-- stored as SHA-256 digests, and tokens and PKCE verifiers as Fernet ciphertext.

CREATE TABLE users (
    id            text PRIMARY KEY,                -- KhadBot's own ID (what job payloads carry)
    wcl_user_id   bigint NOT NULL UNIQUE,          -- the Warcraft Logs account
    name          text NOT NULL,
    created_at    timestamptz NOT NULL,
    last_login_at timestamptz NOT NULL
);

CREATE TABLE sessions (
    id_hash      text PRIMARY KEY,                 -- SHA-256 of the cookie value
    user_id      text NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    created_at   timestamptz NOT NULL,
    expires_at   timestamptz NOT NULL,             -- slides forward on use, never past created_at + the maximum age
    last_seen_at timestamptz NOT NULL
);

CREATE INDEX sessions_user ON sessions (user_id);
CREATE INDEX sessions_expires ON sessions (expires_at);

CREATE TABLE oauth_states (
    state_hash   text PRIMARY KEY,                 -- SHA-256 of the OAuth state
    verifier_enc text NOT NULL,                    -- the PKCE verifier, encrypted
    browser_hash text NOT NULL,                    -- SHA-256 of the login cookie's nonce
    next_path    text NOT NULL,
    expires_at   timestamptz NOT NULL
);

CREATE TABLE wcl_tokens (
    user_id           text PRIMARY KEY REFERENCES users (id) ON DELETE CASCADE,
    refresh_enc       text,                        -- the refresh token, encrypted; null when WCL gave none
    access_enc        text NOT NULL,               -- the short-lived access token, encrypted
    access_expires_at timestamptz,
    updated_at        timestamptz NOT NULL
);

CREATE TABLE report_access (
    user_id     text NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    report_code text NOT NULL,
    readable    boolean NOT NULL,
    via         text CHECK (via IN ('user', 'app')),  -- which token could read it
    checked_at  timestamptz NOT NULL,
    PRIMARY KEY (user_id, report_code)
);

-- Whether the app token (any visitor) can read a report: the public check, shared by every user.
CREATE TABLE report_public_access (
    report_code text PRIMARY KEY,
    readable    boolean NOT NULL,
    checked_at  timestamptz NOT NULL
);
