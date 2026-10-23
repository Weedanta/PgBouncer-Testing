-- R5 login.sql (10%): validate user and token (point SELECT).
-- The token of peserta i is md5('tok_' || i), so pgbench can look it up.
\set p random(1, :peserta)
SELECT p.id, p.nama
FROM r5.token_sesi t
JOIN r5.peserta p ON p.id = t.peserta_id
WHERE t.token_hash = md5('tok_' || :p) AND t.expires_at > now();
