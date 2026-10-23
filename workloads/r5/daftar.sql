-- R5 daftar.sql (4%): participant registration (multi-statement transaction).
-- No hot-row counter here, so the profile is not dominated by artificial row locks.
\set p random(1, :peserta)
\set k random(1, :kompetisi)
BEGIN;
INSERT INTO r5.pendaftaran (peserta_id, kompetisi_id, status, created_at)
VALUES (:p, :k, 'terdaftar', now())
ON CONFLICT (peserta_id, kompetisi_id) DO NOTHING;
INSERT INTO r5.log_aktivitas (peserta_id, jenis, created_at) VALUES (:p, 'daftar', now());
END;
