# Dashboard Nilai Mahasiswa Teknik Komputer — Prototipe

Prototipe web lokal dengan **Python standard library + SQLite** (tanpa package pihak ketiga). Mahasiswa tidak perlu membuat akun. Halaman memvalidasi kombinasi NIM demo dan kode akses di backend, kemudian menampilkan mata kuliah, rincian nilai, nilai sementara berbobot, dan statistik kelas agregat.

## Akun demo

- NIM: `DEMO-240001`
- Kode akses: `TK-DEMO-2026`

Seluruh identitas lain dan nilai pembanding dibuat secara sintetis. Jangan memasukkan nilai atau identitas mahasiswa nyata ke prototipe ini.

## Jalankan secara lokal

Diperlukan Python 3.10 atau yang lebih baru. Tidak perlu `pip install`.

```bash
cd dashboard-nilai-tk
export APP_SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
python3 app.py
```

Buka `http://127.0.0.1:5000` di browser. Database SQLite demo dibuat otomatis pada `instance/dashboard_demo.db` saat startup pertama. Untuk mereset data simulasi, hentikan server lalu hapus file database tersebut dan jalankan ulang.

## Fitur prototipe

- Halaman responsif tanpa ketergantungan pada CDN atau font eksternal.
- Validasi NIM + kode akses di backend; pesan gagal tidak membocorkan apakah NIM ada.
- Kode akses disimpan sebagai hash scrypt, bukan teks asli.
- Sesi berupa cookie bertanda tangan HMAC, `HttpOnly`, `SameSite=Lax`, dan masa berlaku 30 menit.
- Token CSRF pada formulir akses dan logout.
- Pembatasan percobaan login sederhana berbasis IP (in-memory, khusus demo).
- Daftar tiga mata kuliah contoh dengan nilai per komponen.
- Statistik agregat: rata-rata, median, distribusi, dan persentil relatif.
- Nilai individual mahasiswa lain tidak ditampilkan dan data nilai pribadi tidak diletakkan di URL.
- Header keamanan dasar untuk konten, framing, dan referrer.

## Batasan yang wajib diketahui

Ini adalah prototipe demonstrasi, **bukan aplikasi siap produksi**. Belum ada dashboard dosen, impor XLSX/CSV, pembuatan/penggantian kode akses, manajemen pengguna, audit log, atau PostgreSQL. Rate limiting masih in-memory dan hilang saat proses di-restart. Cookie sesi ditandatangani, tetapi tidak dienkripsi; saat ini hanya berisi ID internal, token CSRF, dan waktu kedaluwarsa. Seluruh data berasal dari seed sintetis.

Sebelum penggunaan nyata, minimal lakukan hal berikut:

1. Pindahkan SQLite ke PostgreSQL dan gunakan migrasi skema, backup, serta uji pemulihan.
2. Tambahkan dashboard dosen dengan otorisasi per kelas, impor spreadsheet melalui validasi/pratinjau, commit transaksional, serta audit perubahan nilai.
3. Bangun pembuatan dan rotasi kode akses pribadi dengan entropy tinggi serta distribusi kode yang aman. Jangan menggunakan satu kode bersama untuk satu kelas.
4. Gunakan secret acak dari environment/secret manager; pasang HTTPS dan set `COOKIE_SECURE=1` hanya saat memang dilayani melalui HTTPS.
5. Pindahkan rate limiting ke penyimpanan bersama (misalnya Redis), tambahkan pemantauan, dan review konfigurasi proxy sebelum memercayai alamat IP dari header proxy.
6. Tambahkan pengujian keamanan, otorisasi untuk setiap route dan objek, pengelolaan sesi/revokasi, prosedur pemulihan kode, serta logging yang tidak menyimpan kode akses.
7. Evaluasi ambang minimum ukuran kelompok statistik dan kebijakan privasi bersama institusi.

## Catatan kalkulasi

Lima komponen demo berbobot 15%, 20%, 25%, 20%, dan 20%. Nilai sementara adalah rerata berbobot yang dinormalisasi terhadap bobot komponen yang sudah dipublikasikan. Statistik kelas dihitung dari data sintetis untuk mata kuliah dan kelas yang sama. Nilai yang belum tersedia harus tetap dibedakan dari skor nol apabila fitur nilai parsial ditambahkan di kemudian hari.
