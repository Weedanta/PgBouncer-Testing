-- R5 leaderboard.sql (5%): leaderboard aggregation with SET LOCAL statement_timeout.
-- SET LOCAL (not SET) is safe in transaction mode (Tabel 2.2): it ends with the
-- transaction. The value is a literal because pgbench -M prepared turns :variables
-- into bind parameters, which SET does not accept. 30 s is generous on purpose: the
-- timeout must never fire under normal load; if it does, the pgbench client aborts and
-- the run is flagged (aborted_clients > 0).
\set k random(1, :kompetisi)
BEGIN;
SET LOCAL statement_timeout = '30s';
SELECT peserta_id, skor, rank() OVER (ORDER BY skor DESC, submitted_at) AS peringkat
FROM r5.hasil
WHERE kompetisi_id = :k
ORDER BY skor DESC, submitted_at
LIMIT 50;
END;
