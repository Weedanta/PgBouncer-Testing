# Panduan Playground (Bahasa Indonesia)

Panduan ini menjelaskan apa yang ada di Playground, bagaimana Playground menjalankan
rancangan proposal "Skripsi-Revisi Wedanta (Revisi Bimbingan 20 Okt) v5", dan urutan
perintah untuk menghasilkan bahan Bab 4 (Perancangan dan Implementasi) dan Bab 5 (Hasil
dan Pembahasan). Kode dan dokumen teknis lain
ditulis dalam bahasa Inggris; istilah teknis di sini juga dibiarkan dalam bahasa Inggris.

## 1. Apa itu Playground

Playground adalah **instrumen pengukuran** (Subbab 3.1), bukan produk. Isinya:

- satu stack Docker Compose: PostgreSQL 18, satu PgBouncer `pool_mode = transaction`,
  kontainer klien (pgbench + runner Python), dan monitoring (Gambar 3.3);
- runner yang menjalankan **150 satuan pengukuran** (2 jalur × 5 profil × 5 taraf
  konkurensi × 3 putaran, sekitar 2,5 jam) secara otomatis, acak, dan bisa dilanjutkan
  bila terputus;
- prosedur **penetapan konfigurasi** PgBouncer (RM1, Subbab 3.3) yang menghasilkan laporan
  untuk Subbab 4.4;
- modul **analisis** (Subbab 3.6) yang menghasilkan tabel, gambar, dan laporan Bab 5,
  termasuk memori per koneksi dan kapasitas koneksi pada anggaran memori yang sama.

Prinsip yang tidak boleh dilanggar: tidak ada angka yang dikarang. Semua angka di
`results/` berasal dari run sungguhan; data sintetis hanya ada di `tests/`.

## 2. Peta folder

| Folder/berkas | Isi |
|---|---|
| `experiment.yaml` | Seluruh parameter rancangan (sumber kebenaran tunggal). Nilai bawaan = teks proposal. |
| `docker-compose.yml`, `docker/` | Definisi stack dan image PgBouncer/klien. |
| `config/versions.env` | Tag image yang dipakai (PostgreSQL 18, PgBouncer 1.26.x, exporter). |
| `config/host.env` | Pembagian CPU dan memori per kontainer, ditulis oleh `make probe`. |
| `config/active/` | `postgresql.conf`, `pg_hba.conf`, `pgbouncer.ini` hasil `make render`. |
| `config/locked/` | Konfigurasi yang dikunci setelah kalibrasi (`make lock`). Jangan disunting. |
| `workloads/` | Skrip pgbench: `r2_write.sql`, delapan skrip R5, skema dan seed R5. |
| `playground/` | Kode runner: probe host, render config, reset, runner, kalibrasi, lock. |
| `analysis/` | Statistik, gambar, tabel, laporan. |
| `tools/lint_session_features.py` | Pemeriksa kompatibilitas skrip terhadap Tabel 2.2. |
| `tests/` | Unit test (data sintetis saja). |
| `docs/` | Panduan ini, runbook, keputusan, deviasi, pemetaan ke skripsi, spesifikasi host. |
| `results/` | Semua hasil ukur (tidak masuk git). |

## 3. Arsitektur

```
 Host uji (Linux/WSL2), jaringan bridge "pgnet", cpuset terpisah per kontainer
 ┌──────────────────────── Lapisan klien (pgb-client) ────────────────────────┐
 │ pgbench (R1..R5)                runner Python (urutan acak, pemanasan,     │
 │                                 pengulangan, reset, snapshot, sampler)     │
 └───────┬──────────────────────────────────────────────┬─────────────────────┘
         │ jalur perlakuan :6432                         │ jalur baseline :5432
         ▼                                               │
 ┌───────────────────────────┐                           │
 │ PgBouncer (pgb-pgbouncer) │  pool_mode = transaction  │
 │ pool tetap, dikunci       │                           │
 └───────────┬───────────────┘                           │
             ▼                                           ▼
 ┌─────────────────────────────────────────────────────────────────┐
 │ PostgreSQL 18 (pgb-postgres), max_connections = 1100,           │
 │ data di disk lokal host (bind mount)                            │
 └─────────────────────────────────────────────────────────────────┘
 Monitoring: postgres_exporter :9187, pgbouncer_exporter :9127, node_exporter :9100,
 cAdvisor :8080 → Prometheus :9090 (5 detik) → Grafana :3000 (hanya dua port ini dibuka,
 hanya di 127.0.0.1).
 Penyimpanan: runner menulis results/<kampanye>/ (runs.csv, log pgbench .zst,
 samples.csv, ekspor Prometheus .parquet per run). Modul analisis (Python + pandas)
 berjalan di host setelah pengujian dan hanya membaca berkas-berkas itu.
```

Grafana hanya untuk memantau saat pengujian berjalan. Semua angka di skripsi diambil dari
`results/`, bukan dari dasbor.

Pembagian CPU mengikuti Subbab 3.4: setiap kontainer punya inti sendiri. `make probe`
membaca topologi CPU, menjaga dua thread satu inti fisik tetap di kontainer yang sama,
lalu membagi: 1 inti untuk OS/Docker, 1 untuk monitoring, 1 untuk PgBouncer (prosesnya
tunggal), 25 % sisa untuk klien, dan sisanya untuk PostgreSQL. Pada laptop 8 inti:
PostgreSQL mendapat 3 inti fisik, sehingga aturan Tabel 3.2 memberi
`default_pool_size = 2 × 3 + 0 = 6`.

## 4. Dari tahapan penelitian ke perintah

| Tahap (Gambar 3.1) | Perintah | Hasil |
|---|---|---|
| Implementasi lingkungan uji | `make sync probe secrets build up` | Stack berjalan, `docs/HOST.md` |
| Validasi lingkungan uji | `make init-data-smoke smoke`, `make check-metrics`, `make evidence` | Kampanye `SMOKE-...` dan analisisnya |
| Data uji | `make init-data` | Template `bench_template` (scale 100, sekitar 1,5 GB, + R5) |
| Penetapan konfigurasi (RM1) | `make calibrate`, bila perlu `make adjust`, lalu `make lock` | `results/calibration/<waktu>/REPORT.md`, `config/locked/` |
| Pengujian kinerja | `make campaign CAMPAIGN=C1` | `results/C1/runs.csv` + folder per run |
| Analisis dan uji hipotesis (RM2) | `make analyze export-thesis CAMPAIGN=C1` | `results/C1/analysis/`, `results/C1/thesis/` |

Perintah lengkap dan persiapan WSL2 ada di [RUNBOOK.md](RUNBOOK.md).

## 5. Penetapan konfigurasi (RM1) secara rinci

`make calibrate` menjalankan Gambar 3.2:

1. **Langkah 1, kajian dokumentasi.** Menghitung `default_pool_size` dari aturan
   (2 × inti fisik PostgreSQL + spindle efektif) dan memeriksa semua skrip terhadap
   Tabel 2.2 (berkas `compatibility_lint.md`).
2. **Langkah 2, pengamatan awal** (30 detik setelah pemanasan 5 detik, c = 100 dan
   1.000, lewat PgBouncer):
   - *Pemeriksaan 1*: R1 sampai R5 berjalan tanpa transaksi gagal dan tanpa klien aborted.
   - *Pemeriksaan 2*: satu klien menjalankan 50 transaksi `SELECT pg_backend_pid()`
     sementara ada beban latar. Lewat PgBouncer harus muncul lebih dari satu PID; lewat
     jalur langsung (kontrol) hanya satu PID.
   - *Pemeriksaan 3*: tidak ada error batas koneksi atau `query_wait_timeout`, baik di
     keluaran pgbench maupun di log PgBouncer.
   - *Pemeriksaan 4*: R3 dan R5 dijalankan dengan pool P/2, P, dan 2P (3 kali, urutan
     acak). Lolos bila median TPS pada P minimal 95 % dari yang terbaik dan median p95
     pada P maksimal 110 % dari yang terbaik (keputusan D3).
3. **Langkah 3, penguncian.** Bila semua lolos, `make lock` menyimpan konfigurasi, hash
   skrip, dan versi yang benar-benar berjalan, lalu membuat tag git.

Bila ada pemeriksaan yang gagal, proposal mengizinkan **satu** penyesuaian per nilai
dengan alasan tertulis: `make adjust PARAM=default_pool_size VALUE=8 REASON="..."`,
`make reload`, lalu `make calibrate` lagi. Penyesuaian kedua untuk nilai yang sama
ditolak. Semua penyesuaian tercatat di `config/calibration_overrides.yaml` dan muncul di
laporan.

`REPORT.md` kalibrasi ditulis dalam bahasa Indonesia dan sudah memuat isian Tabel 3.2
dengan nilai akhir, jadi bisa langsung dipakai sebagai jawaban RM1 di Subbab 4.4.

## 6. Satu satuan pengukuran

Setiap baris rencana (`plan.csv`) dijalankan dengan urutan ini:

1. pemeriksaan cepat: konfigurasi terkunci tidak berubah, PostgreSQL tidak restart;
2. reset basis data dari template bila profil menulis data (R2 sampai R5);
3. koneksi pool PgBouncer ditutup (`KILL`/`RESUME`), `CHECKPOINT`, `pg_stat_reset()`;
4. pemanasan 10 detik dengan perintah pgbench yang identik (tidak dicatat);
5. pengukuran 30 detik dengan `--log` per transaksi; sampler mencatat jumlah backend,
   jumlah sesi yang menunggu lock, dan status pool tiap 5 detik;
6. snapshot statistik sesudah run (termasuk jumlah deadlock), pemeriksaan invarian R5,
   ekspor Prometheus, dan rata-rata RSS memori kontainer PostgreSQL dan PgBouncer;
7. log diolah menjadi TPS, rata-rata, simpangan baku, p50, p95, p99 (juga per skrip R5),
   lalu satu baris ditambahkan ke `runs.csv`.

Dua jalur dalam satu sel (profil, konkurensi, putaran) dijalankan berurutan dengan urutan
acak dan memakai seed pgbench yang sama, sehingga keduanya menjadi satu **pasangan**.

Isi folder `results/<kampanye>/runs/<run_id>/`: `meta.json` (perintah lengkap, versi,
hasil), `warmup.stdout`, `measure.stdout`, log pgbench terkompresi (`*.zst`),
`latency_hist.parquet`, `latency_per_script.csv`, `samples.csv`, `prometheus.parquet`,
dan snapshot sebelum/sesudah.

`runs.csv` hanya bisa ditambah (append-only) dan dijaga checksum. Setiap percobaan,
termasuk yang gagal atau terputus, tercatat di `attempts.csv`. Kegagalan infrastruktur
diulang paling banyak sekali; hasil sistem yang diuji (transaksi gagal, klien aborted,
latensi tinggi) adalah data dan tidak pernah diulang.

## 7. Flag validitas

Run bertanda **tidak pernah dibuang otomatis** (Subbab 3.8). Analisis utama memakai semua
run, dan tabel sensitivitas tanpa pasangan bertanda dibuat terpisah.

| Flag | Arti |
|---|---|
| `flag_client_cpu_saturated` | CPU kontainer klien ≥ 95 % cpuset: pgbench mungkin menjadi titik jenuh |
| `flag_pgbouncer_cpu_saturated` | CPU PgBouncer ≥ 95 % satu inti |
| `flag_failed_tx`, `flag_aborted_clients` | Ada transaksi gagal atau klien pgbench berhenti |
| `flag_checkpoint_during_run` | Ada checkpoint di dalam jendela pengukuran |
| `flag_log_count_mismatch` | Jumlah transaksi di log tidak sama dengan hitungan pgbench |
| `flag_invariant_failed` | Invarian R5 gagal (nomor peserta berlubang, hasil tanpa sesi) |
| `flag_container_restarted` | PostgreSQL restart selama run |
| `flag_prometheus_missing` | Data Prometheus tidak tersedia untuk run ini |

## 8. Membaca hasil analisis

- **Model.** Untuk setiap profil dan metrik dipasang ANOVA dua faktor dengan pengulangan
  (Jain, 1991): faktor jalur (2 taraf), faktor konkurensi (5 taraf), 3 pengulangan. Model
  dihitung pada ln(nilai) karena TPS dan latensi berubah secara proporsional. Hasilnya di
  `06_anova.csv`: F, p, dan partial η² untuk jalur, konkurensi, dan interaksinya.
- **Δ%** adalah selisih relatif rata-rata geometrik jalur PgBouncer terhadap jalur
  langsung pada satu taraf, lengkap dengan SK 95% (`07_delta_by_level.csv`). Δ positif
  pada TPS dan negatif pada latensi berarti kinerja membaik.
- **H1** (TPS) dan **H2** (p95) diuji pada c = 500 dan 1.000 untuk tiap profil dengan uji
  kontras satu sisi yang memakai galat gabungan model (df = 20). Ada 20 perbandingan;
  nilai p dikoreksi Holm. H0 ditolak bila p Holm < 0,05.
- **Interaksi jalur × konkurensi** menjawab pertanyaan "apakah dampak PgBouncer berubah
  seiring naiknya konkurensi" (Subbab 2.7). Bila p interaksi kecil, dampaknya memang
  bergantung pada jumlah klien.
- **Prasyarat**: Shapiro-Wilk pada residu model dan Levene antarsel. Kombinasi yang
  menolak prasyarat tetap dilaporkan dan dibaca dengan hati-hati.
- **Memori per koneksi** (`13_memory_per_connection.csv`): kemiringan garis regresi RSS
  terhadap jumlah koneksi. Jalur langsung: RSS PostgreSQL terhadap jumlah backend. Jalur
  PgBouncer: RSS PgBouncer terhadap jumlah klien. RSS tidak memuat `shared_buffers`.
- **Kapasitas** (`14_capacity.csv`, `capacity_by_budget.png`): berapa koneksi klien yang
  muat pada anggaran 256, 512, dan 1.024 MB di tiap jalur. Ini contoh 1 GB dari Pak Nanang.
  Angka di atas 1.000 adalah ekstrapolasi, dan jalur PgBouncer tetap dibatasi
  `max_client_conn`.
- **Memori dan throughput per taraf** (`15_memory_by_level.csv`) menyandingkan memori
  yang dipakai koneksi dengan TPS dan p95 di setiap taraf. Tabel ini dipakai untuk
  membahas trade-off di Bab 5.
- **Perebutan lock** (Subbab 3.5.2): dengan scale 100, `pgbench_branches` hanya berisi
  100 baris, padahal R3 dan R4 memperbaruinya di setiap transaksi. Tabel
  `11_lock_contention.csv`, gambar `support_lock_waiting.png`, dan bagian 5 `REPORT.md`
  menyandingkan sesi yang menunggu lock dan deadlock R3/R4 dengan R1, R2, dan R5. Pakai
  data ini di Bab 5 agar efek perebutan lock tidak dibaca sebagai efek penanganan koneksi
  semata.
- **Perintah pgbench** setiap satuan pengukuran (port tujuan, skrip, `-j`, `-C`) ada di
  `12_commands.csv` untuk Subbab 4.3.
- `REPORT.md` menuliskan di mana Δ% berubah tanda. Penafsiran tetap ditulis
  sendiri di Bab 5.

## 9. Temuan penting yang harus dibahas dengan pembimbing

1. **pgbench `-M prepared` macet di belakang pooler mode transaction (DEV-013).**
   pgbench menyiapkan setiap statement dengan `PQprepare()` yang memblokir thread. Bila
   klien lebih banyak dari pool, satu klien yang menunggu koneksi pool membekukan seluruh
   thread-nya, termasuk klien lain di thread itu yang sedang memegang koneksi pool di
   tengah transaksi. Akibatnya semua menunggu sampai `query_wait_timeout` (120 detik).
   Ini terbukti pada pgbench 16 dan kode sumber pgbench 18.6, dengan PgBouncer 1.22.0
   maupun 1.26.0, untuk semua profil multi-statement (R2, R3, R4, sebagian R5).
   Solusi bawaan Playground: **satu thread pgbench per klien (`-j = -c`) di kedua
   jalur**, sehingga `-M prepared` (Tabel 3.7) tetap dipakai. Konsekuensinya Tabel 3.7
   baris "Jumlah utas pgbench" perlu direvisi, dan CPU klien dipantau lewat flag.
   Alternatif: tetap `-j` = jumlah inti tetapi memakai `-M extended`.
2. **Scale factor 100 (Subbab 3.5.2).** Dataset sekitar 1,5 GB dan termuat
   di memori, sehingga spindle efektif = 0. Konsekuensinya, R3/R4 pada c > 100 menghadapi
   perebutan lock baris di `pgbench_branches`. Playground mencatat sesi yang menunggu lock
   dan deadlock per run. `make evidence` menampilkan bukti bahwa dataset memang termuat di
   memori (disk read selama pengukuran mendekati nol).
3. **Host kampanye** (DEV-008): laptop Windows + WSL2 bisa dipakai, tetapi pembagian CPU
   di WSL2 memetakan vCPU, bukan inti fisik yang dijamin. Kampanye sekarang hanya sekitar
   2,5 jam, jadi risiko throttling termal jauh lebih kecil, tetapi tetap jalankan saat
   laptop tercolok listrik dan tidak dipakai untuk hal lain.
4. **Durasi pengukuran 30 detik (D16).** Setelah run pertama, lihat baris progres 5
   detik di `measure.stdout`. Bila TPS masih naik turun tajam di dalam jendela 30 detik,
   naikkan `design.measure_s` ke 60 (kampanye bertambah sekitar 40 menit) sebelum
   kampanye sungguhan.

Daftar lengkap ada di [DEVIATIONS.md](DEVIATIONS.md) dan [DECISIONS.md](DECISIONS.md).

## 10. Masalah umum

| Gejala | Penyebab dan solusi |
|---|---|
| `make probe`: postgres would get only ... MB | Memori WSL terlalu kecil. Atur `.wslconfig` (RUNBOOK bagian 1). |
| `make up` gagal di PgBouncer | `make render` belum dijalankan, atau `secrets/` belum dibuat (`make secrets`). |
| `make check-metrics` menandai seri hilang | Nama metrik exporter berubah. Sesuaikan `config/prometheus/series.yaml`. |
| Runner menolak mulai: "locked input changed" | Ada berkas terkunci yang berubah. Kembalikan, atau ulangi kalibrasi dan lock. |
| Run berhenti di tengah | `make resume CAMPAIGN=...`. Percobaan lama tetap tersimpan. |
| `make lock` menolak | Belum ada laporan kalibrasi yang lolos. |
