-- R5 ambil_soal.sql (25%): fetch a question with the saved answer (SELECT with JOIN).
\set s random(1, :peserta)
\set no random(1, :soal)
SELECT so.isi, so.opsi, j.jawaban
FROM r5.sesi_pengerjaan sp
JOIN r5.soal so ON so.kompetisi_id = sp.kompetisi_id AND so.no = :no
LEFT JOIN r5.jawaban j ON j.sesi_id = sp.id AND j.no_soal = :no
WHERE sp.id = :s;
