-- R5 autosave.sql (35%): automatic answer save, INSERT ... ON CONFLICT DO UPDATE.
-- One sesi per peserta, so sesi ids run from 1 to :peserta.
\set s random(1, :peserta)
\set no random(1, :soal)
\set j random(1, 5)
INSERT INTO r5.jawaban (sesi_id, no_soal, jawaban, updated_at)
VALUES (:s, :no, :j, now())
ON CONFLICT (sesi_id, no_soal)
DO UPDATE SET jawaban = EXCLUDED.jawaban, updated_at = EXCLUDED.updated_at;
