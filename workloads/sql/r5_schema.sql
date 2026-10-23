-- R5 schema: compact online-competition model (namespace r5) used by the R5 scripts.
-- Table names stay Indonesian because they model the case domain (Subbab 1.1).
-- psql variable: :sesi_fillfactor
CREATE SCHEMA IF NOT EXISTS r5;

CREATE TABLE r5.peserta (
    id          bigint PRIMARY KEY,
    nama        text NOT NULL,
    email       text NOT NULL,
    created_at  timestamptz NOT NULL
);

CREATE TABLE r5.token_sesi (
    token_hash  text PRIMARY KEY,
    peserta_id  bigint NOT NULL,
    expires_at  timestamptz NOT NULL
);

CREATE TABLE r5.kompetisi (
    id          int PRIMARY KEY,
    nama        text NOT NULL,
    mulai       timestamptz NOT NULL,
    selesai     timestamptz NOT NULL
);

CREATE TABLE r5.soal (
    kompetisi_id int NOT NULL,
    no           int NOT NULL,
    isi          text NOT NULL,
    opsi         jsonb NOT NULL,
    kunci        smallint NOT NULL,
    PRIMARY KEY (kompetisi_id, no)
);

-- fillfactor < 100 leaves room for HOT updates by heartbeat.sql; last_heartbeat is
-- deliberately not indexed.
CREATE TABLE r5.sesi_pengerjaan (
    id              bigint PRIMARY KEY,
    peserta_id      bigint NOT NULL,
    kompetisi_id    int NOT NULL,
    status          text NOT NULL,
    last_heartbeat  timestamptz,
    submitted_at    timestamptz
) WITH (fillfactor = :sesi_fillfactor);

CREATE TABLE r5.jawaban (
    sesi_id     bigint NOT NULL,
    no_soal     int NOT NULL,
    jawaban     smallint NOT NULL,
    updated_at  timestamptz NOT NULL,
    PRIMARY KEY (sesi_id, no_soal)
);

CREATE TABLE r5.pendaftaran (
    peserta_id   bigint NOT NULL,
    kompetisi_id int NOT NULL,
    status       text NOT NULL,
    created_at   timestamptz NOT NULL,
    PRIMARY KEY (peserta_id, kompetisi_id)
);

CREATE TABLE r5.log_aktivitas (
    id          bigserial PRIMARY KEY,
    peserta_id  bigint NOT NULL,
    jenis       text NOT NULL,
    created_at  timestamptz NOT NULL
);

CREATE TABLE r5.hasil (
    sesi_id      bigint PRIMARY KEY,
    kompetisi_id int NOT NULL,
    peserta_id   bigint NOT NULL,
    skor         int NOT NULL,
    submitted_at timestamptz NOT NULL
);
CREATE INDEX hasil_leaderboard_idx ON r5.hasil (kompetisi_id, skor DESC, submitted_at);

CREATE TABLE r5.nomor_counter (
    kompetisi_id int PRIMARY KEY,
    last_no      int NOT NULL
);

CREATE TABLE r5.nomor_peserta (
    kompetisi_id int NOT NULL,
    nomor        int NOT NULL,
    peserta_id   bigint NOT NULL,
    PRIMARY KEY (kompetisi_id, nomor)
);
