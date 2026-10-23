-- Post-run invariants for R5 runs (checked by the runner, never fixed).
-- Each row is (check, ok, detail).
SELECT 'nomor_peserta_no_gaps' AS check_name,
       bool_and(np.cnt = c.last_no AND COALESCE(np.max_no, 0) = c.last_no) AS ok,
       json_agg(json_build_object('k', c.kompetisi_id, 'last_no', c.last_no,
                                  'count', np.cnt, 'max', np.max_no))
         FILTER (WHERE np.cnt <> c.last_no OR COALESCE(np.max_no, 0) <> c.last_no)::text AS detail
FROM r5.nomor_counter c
LEFT JOIN LATERAL (
    SELECT count(*) AS cnt, max(nomor) AS max_no
    FROM r5.nomor_peserta n WHERE n.kompetisi_id = c.kompetisi_id
) np ON true
UNION ALL
SELECT 'hasil_references_sesi',
       NOT EXISTS (SELECT 1 FROM r5.hasil h
                   LEFT JOIN r5.sesi_pengerjaan s ON s.id = h.sesi_id
                   WHERE s.id IS NULL),
       NULL;
