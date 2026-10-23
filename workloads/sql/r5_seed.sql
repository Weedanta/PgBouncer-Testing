-- R5 seed with a fixed random seed (deterministic). psql variables:
--   :seed (in [-1, 1]), :peserta, :kompetisi, :soal, :jawaban_fraction, :hasil_fraction
-- One sesi per peserta: sesi.id = peserta.id, kompetisi_id = (id % kompetisi) + 1.
\set ON_ERROR_STOP on
-- Serial plans only: with parallel workers the random() call order would not be fixed.
SET max_parallel_workers_per_gather = 0;
SELECT setseed(:seed);

INSERT INTO r5.kompetisi (id, nama, mulai, selesai)
SELECT k, 'Kompetisi ' || k, timestamptz '2026-01-01 08:00+07', timestamptz '2099-12-31 17:00+07'
FROM generate_series(1, :kompetisi) AS k;

INSERT INTO r5.soal (kompetisi_id, no, isi, opsi, kunci)
SELECT k, n,
       'Soal ' || n || ' kompetisi ' || k || ': ' || repeat('teks soal ', 20),
       jsonb_build_array('A', 'B', 'C', 'D', 'E'),
       (1 + floor(random() * 5))::smallint
FROM generate_series(1, :kompetisi) AS k, generate_series(1, :soal) AS n
ORDER BY k, n;

INSERT INTO r5.peserta (id, nama, email, created_at)
SELECT i, 'Peserta ' || i, 'peserta' || i || '@contoh.test', timestamptz '2026-01-01 00:00+07'
FROM generate_series(1, :peserta) AS i;

INSERT INTO r5.token_sesi (token_hash, peserta_id, expires_at)
SELECT md5('tok_' || i), i, timestamptz '2099-12-31 00:00+07'
FROM generate_series(1, :peserta) AS i;

INSERT INTO r5.pendaftaran (peserta_id, kompetisi_id, status, created_at)
SELECT i, (i % :kompetisi) + 1, 'terdaftar', timestamptz '2026-01-01 00:00+07'
FROM generate_series(1, :peserta) AS i;

INSERT INTO r5.sesi_pengerjaan (id, peserta_id, kompetisi_id, status, last_heartbeat, submitted_at)
SELECT i, i, (i % :kompetisi) + 1, 'berjalan', timestamptz '2026-01-01 08:00+07', NULL
FROM generate_series(1, :peserta) AS i;

-- jawaban for a fixed fraction of (sesi, soal) pairs. With a serial plan the
-- generate_series scan order, and so the random() sequence, is fixed.
INSERT INTO r5.jawaban (sesi_id, no_soal, jawaban, updated_at)
SELECT s, n, (1 + floor(random() * 5))::smallint, timestamptz '2026-01-01 08:30+07'
FROM (
    SELECT s, n, random() AS r
    FROM generate_series(1, :peserta) AS s, generate_series(1, :soal) AS n
) x
WHERE r < :jawaban_fraction;

INSERT INTO r5.hasil (sesi_id, kompetisi_id, peserta_id, skor, submitted_at)
SELECT s, (s % :kompetisi) + 1, s, floor(random() * (:soal + 1))::int,
       timestamptz '2026-01-01 10:00+07' + make_interval(secs => floor(random() * 7200))
FROM (
    SELECT s, random() AS r FROM generate_series(1, :peserta) AS s
) x
WHERE r < :hasil_fraction;

UPDATE r5.sesi_pengerjaan sp SET status = 'selesai', submitted_at = h.submitted_at
FROM r5.hasil h WHERE h.sesi_id = sp.id;

INSERT INTO r5.nomor_counter (kompetisi_id, last_no)
SELECT k, 0 FROM generate_series(1, :kompetisi) AS k;
