-- R2 "Tulis" (Tabel 3.5): one transaction with UPDATE + INSERT, persistent connection.
-- :scale is passed explicitly with `pgbench -s <scale>` because pgbench does not detect
-- the scale factor for custom scripts.
\set aid random(1, 100000 * :scale)
\set bid random(1, 1 * :scale)
\set tid random(1, 10 * :scale)
\set delta random(-5000, 5000)
BEGIN;
UPDATE pgbench_accounts SET abalance = abalance + :delta WHERE aid = :aid;
INSERT INTO pgbench_history (tid, bid, aid, delta, mtime) VALUES (:tid, :bid, :aid, :delta, CURRENT_TIMESTAMP);
END;
