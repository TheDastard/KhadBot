-- 0002_read_models.sql
-- Read models: small per-pull tables projected from each pull's stored records (db/read_models.py), which back the
-- night, report and progression views. Full detail stays in the records blob; these hold what the views filter,
-- sort or aggregate on. A pull's rows are replaced together whenever its records hash changes.

CREATE TABLE reports (
    code          text PRIMARY KEY,
    title         text,
    start_time_ms bigint,              -- report startTime, ms since the epoch; orders nights
    guild_id      bigint,              -- the WCL guild, when the report metadata has one
    guild_name    text,
    visibility    text,
    fetched_with  text CHECK (fetched_with IN ('user', 'app')),
    fetched_at    timestamptz
);

CREATE TABLE pulls (
    report_code          text NOT NULL REFERENCES reports (code) ON DELETE CASCADE,
    fight_id             integer NOT NULL,
    pull_key             text NOT NULL,
    encounter_id         integer NOT NULL,
    encounter_name       text NOT NULL,
    difficulty           integer,
    kill                 boolean NOT NULL,
    start_ms             bigint NOT NULL,
    end_ms               bigint NOT NULL,
    duration_ms          bigint NOT NULL,
    boss_pct             double precision,
    boss_hp_pct          double precision,
    phase_reached        text,
    phase_name           text,
    has_encounter_file   boolean NOT NULL,
    coverage             text NOT NULL CHECK (coverage IN ('full', 'partial', 'none')),
    cutoff_ms            bigint,
    deaths               integer NOT NULL,
    deaths_before_cutoff integer NOT NULL,
    anomalies            text[] NOT NULL,
    events_hash          text NOT NULL,
    encfile_hash         text NOT NULL,
    records_hash         text NOT NULL,
    records_key          text NOT NULL,
    records_version      integer NOT NULL,
    read_model_version   integer NOT NULL,
    projected_at         timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (report_code, fight_id)
);

-- Progression: one encounter and difficulty across reports.
CREATE INDEX pulls_encounter ON pulls (encounter_id, difficulty, report_code);

CREATE TABLE pull_deaths (
    report_code          text NOT NULL,
    fight_id             integer NOT NULL,
    n                    integer NOT NULL,  -- 1-based, time order: the ``death:<n>`` ref
    ref                  text NOT NULL,
    actor_id             integer NOT NULL,
    name                 text NOT NULL,
    t_ms                 bigint NOT NULL,
    phase_id             text,
    killing_ability_id   integer,
    killing_ability_name text,
    overkill             bigint,
    time_to_die_ms       bigint,
    after_cutoff         boolean NOT NULL,
    PRIMARY KEY (report_code, fight_id, n),
    FOREIGN KEY (report_code, fight_id) REFERENCES pulls (report_code, fight_id) ON DELETE CASCADE
);

CREATE TABLE pull_mechanics (
    report_code      text NOT NULL,
    fight_id         integer NOT NULL,
    mechanic_id      text NOT NULL,
    name             text,              -- the encounter file's name for the mechanic
    ref              text NOT NULL,
    clusters         integer NOT NULL,
    hits             integer NOT NULL,
    failures         integer NOT NULL,
    damage_taken     bigint NOT NULL,
    deaths_in_window integer NOT NULL,
    PRIMARY KEY (report_code, fight_id, mechanic_id),
    FOREIGN KEY (report_code, fight_id) REFERENCES pulls (report_code, fight_id) ON DELETE CASCADE
);

CREATE TABLE pull_cooldowns (
    report_code text NOT NULL,
    fight_id    integer NOT NULL,
    ref         text NOT NULL,
    spell_id    integer NOT NULL,
    spell_name  text NOT NULL,
    category    text NOT NULL CHECK (category IN ('defensive', 'offensive', 'utility')),
    caster      text NOT NULL,
    t_ms        bigint NOT NULL,
    phase_id    text,
    PRIMARY KEY (report_code, fight_id, ref),
    FOREIGN KEY (report_code, fight_id) REFERENCES pulls (report_code, fight_id) ON DELETE CASCADE
);

CREATE TABLE pull_windows (
    report_code                text NOT NULL,
    fight_id                   integer NOT NULL,
    window_id                  text NOT NULL,
    occurrence                 integer NOT NULL,
    name                       text,     -- the encounter file's name for the damage window
    ref                        text NOT NULL,
    t_ms                       bigint NOT NULL,
    covered                    boolean NOT NULL,
    defensive_cooldowns_active bigint[] NOT NULL,
    defensive_cooldowns_late   integer NOT NULL,  -- how many started late; the blob has which and by how much
    PRIMARY KEY (report_code, fight_id, window_id, occurrence),
    FOREIGN KEY (report_code, fight_id) REFERENCES pulls (report_code, fight_id) ON DELETE CASCADE
);
