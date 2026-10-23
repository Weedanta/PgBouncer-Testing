-- R5 heartbeat.sql (15%): mark the work session as alive (UPDATE). last_heartbeat is
-- not indexed and the table has fillfactor 90, so this can be a HOT update.
\set s random(1, :peserta)
UPDATE r5.sesi_pengerjaan SET last_heartbeat = now() WHERE id = :s;
