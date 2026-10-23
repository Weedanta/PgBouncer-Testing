-- R5 nomor_peserta.sql (2%): assign a participant number under
-- pg_advisory_xact_lock() (transaction-scoped). The session-scoped pg_advisory_lock()
-- is NOT used: in transaction mode it would stick to a pooled backend (Tabel 2.2).
-- 7305 is the lock namespace of this application.
\set p random(1, :peserta)
\set k random(1, :kompetisi)
BEGIN;
SELECT pg_advisory_xact_lock(7305, :k);
UPDATE r5.nomor_counter SET last_no = last_no + 1 WHERE kompetisi_id = :k RETURNING last_no AS nomor \gset
INSERT INTO r5.nomor_peserta (kompetisi_id, nomor, peserta_id) VALUES (:k, :nomor, :p);
END;
