-- migrate:up
-- migrate:up
CREATE TABLE steam_user (
    id SERIAL PRIMARY KEY,
    steam_id BIGINT NOT NULL UNIQUE,
    profile_url TEXT NOT NULL,
    avatar32 TEXT NOT NULL,
    avatar64 TEXT NOT NULL,
    avatar184 TEXT NOT NULL,
    username TEXT NOT NULL,
    fullname TEXT NOT NULL,
    current_game_id BIGINT DEFAULT 0,
    current_game_name TEXT DEFAULT '',
    register_time BIGINT DEFAULT 0
);

CREATE TABLE steam_game (
    id SERIAL PRIMARY KEY,
    steam_id BIGINT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    play_time BIGINT NOT NULL,
    last_play_time BIGINT NOT NULL,
    icon TEXT NOT NULL,
    perfect BOOLEAN DEFAULT false
);

CREATE TABLE steam_achievement (
    id SERIAL PRIMARY KEY,
    steam_id TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    icon TEXT NOT NULL,
    completed BOOLEAN NOT NULL,
    unlock_time BIGINT NOT NULL,
    game_id INTEGER,
    rarity FLOAT8 DEFAULT NULL,
    icon_gray TEXT DEFAULT NULL,
    hidden BOOLEAN DEFAULT false,
    FOREIGN KEY (game_id) REFERENCES steam_game(id) ON DELETE CASCADE,
    UNIQUE (steam_id, game_id)
);

CREATE TABLE steam_sync (
    id SERIAL PRIMARY KEY,
    last_time BIGINT DEFAULT 0
);

INSERT INTO steam_sync (last_time) VALUES (0);

CREATE TABLE steam_completion (
    id SERIAL PRIMARY KEY,
    complete_time FLOAT8 NOT NULL,
    completion FLOAT8 NOT NULL,
    completed INTEGER NOT NULL,
    perfect INTEGER NOT NULL,
    total INTEGER NOT NULL,
    average_completion FLOAT8 DEFAULT 0.0
);

CREATE TABLE steam_completion_insert_modification (
    id SERIAL PRIMARY KEY,
    target_table TEXT NOT NULL,
    payload TEXT NOT NULL, -- You could also use JSONB here if the payload is structured JSON
    completion_id INTEGER,
    FOREIGN KEY (completion_id) REFERENCES steam_completion(id) ON DELETE CASCADE
);

CREATE TABLE steam_completion_update_modification (
    id SERIAL PRIMARY KEY,
    target_table TEXT NOT NULL,
    target_column TEXT NOT NULL,
    old_value BYTEA NOT NULL, -- Postgres uses BYTEA instead of BLOB
    new_value BYTEA NOT NULL,
    completion_id INTEGER,
    FOREIGN KEY (completion_id) REFERENCES steam_completion(id) ON DELETE CASCADE
);


-- migrate:down

