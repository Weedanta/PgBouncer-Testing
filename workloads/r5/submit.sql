-- R5 submit.sql (4%): final submission and scoring (multi-statement transaction).
-- Lock order is always sesi_pengerjaan -> hasil, so it cannot deadlock with itself
-- or with heartbeat.sql.
\set s random(1, :peserta)
BEGIN;
UPDATE r5.sesi_pengerjaan SET status = 'selesai', submitted_at = now() WHERE id = :s;
INSERT INTO r5.hasil (sesi_id, kompetisi_id, peserta_id, skor, submitted_at)
SELECT sp.id, sp.kompetisi_id, sp.peserta_id,
       COALESCE(SUM(CASE WHEN j.jawaban = so.kunci THEN 1 ELSE 0 END), 0)::int,
       now()
FROM r5.sesi_pengerjaan sp
LEFT JOIN r5.jawaban j ON j.sesi_id = sp.id
LEFT JOIN r5.soal so ON so.kompetisi_id = sp.kompetisi_id AND so.no = j.no_soal
WHERE sp.id = :s
GROUP BY sp.id, sp.kompetisi_id, sp.peserta_id
ON CONFLICT (sesi_id) DO UPDATE SET skor = EXCLUDED.skor, submitted_at = EXCLUDED.submitted_at;
END;
