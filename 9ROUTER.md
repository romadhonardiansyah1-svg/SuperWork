# SuperWork dengan 9router lokal

Gunakan OpenCode yang sudah terpasang untuk menjalankan workflow SuperWork. Helper ini memakai Python standar dan mengambil daftar model langsung dari 9router. Tidak ada model cloud yang dipilih otomatis.

## Mulai

Jalankan dari folder SuperWork, setelah server 9router aktif. Jika perlu memulainya:

```powershell
9router --host 127.0.0.1 --no-browser --skip-update
```

Lihat model yang tersedia, lalu pilih ID persis seperti yang ditampilkan:

```powershell
python tools/superwork.py models
python tools/superwork.py check --model "ag/gemini-3.8-flash"
python tools/superwork.py start --model "ag/gemini-3.8-flash"
```

Ganti ID di atas dengan pilihan dari router Anda. Model yang tercantum belum tentu memiliki kuota atau mendukung semua fitur. Jalankan `check` untuk setiap model yang hendak digunakan.

Di OpenCode, `/models` menampilkan pilihan router. Perintah `/setup`, `/scrape`, `/rank`, `/apply`, dan perintah lain merujuk langsung ke `.claude/commands/`. Profil dan metodologi tetap berasal dari `CLAUDE.md` serta `.claude/skills/`; aturan tersebut tidak disalin ke helper. Pengubahan berkas dan perintah shell tetap meminta persetujuan runtime.

## Pilihan dan autentikasi

Pilihan model juga dapat disimpan untuk terminal saat ini:

```powershell
$env:SUPERWORK_MODEL = "ag/gemini-3.8-flash"
python tools/superwork.py check
python tools/superwork.py start
```

Alamat bawaan adalah `http://127.0.0.1:20128/v1`. Port lokal lain dapat diberikan dengan `--base-url` atau `SUPERWORK_9ROUTER_URL`. Helper menerima endpoint loopback tanpa user/password, query, atau fragment. Model tetap berjalan pada provider yang dipilih melalui router.

Untuk inferensi, helper mengutamakan `SUPERWORK_9ROUTER_API_KEY`. Jika variabel itu kosong, helper meminta kunci aktif melalui client CLI resmi dari instalasi 9router lokal. Tidak ada kunci atau akun baru yang dibuat. Kunci tidak dicetak, tidak ditulis ke konfigurasi proyek, dan tidak dibaca dari `.env`. Jika penemuan otomatis tidak tersedia, masukkan kunci dari dashboard ke environment terminal menggunakan input tersembunyi:

```powershell
$routerCredential = Get-Credential -UserName "9router" -Message "Masukkan API key 9router pada kolom Password"
$env:SUPERWORK_9ROUTER_API_KEY = $routerCredential.GetNetworkCredential().Password
```

## Apa yang dibuktikan check

`check` memakai folder sementara dan repositori Git sementara. Git diperlukan untuk memberi OpenCode batas izin baca yang tepat. Konfigurasi, skill, dan plugin global dipisahkan dari sesi uji. Tidak ada CV atau profil pribadi yang dimuat.

Model wajib memanggil tool `read` untuk satu berkas contoh. Berkas berisi kandidat fiktif dengan kemampuan Python, lowongan yang memerlukan Python dan SQL, serta nilai acak yang hanya ada di berkas tersebut. Semua tool lain ditolak.

Helper menerima hasil hanya jika event pembacaan berkas selesai dan pesan akhir berupa JSON valid dengan tipe/nilai yang benar: Python cocok, SQL belum dimiliki, profil belum lengkap, dan `language_gate` bernilai string `FLAG`. Nilai acak harus sama. Markdown, nilai karangan, tool gagal, JSON salah, error provider, atau timeout menghasilkan exit code 1. Tidak ada fallback parser atau pencatatan PASS untuk kegagalan tersebut.

JSON dihasilkan melalui model dan diperiksa secara deterministik di sisi klien. Ini tidak mengklaim setiap provider menerapkan fitur native `response_format: json_schema`. Timeout uji adalah 120 detik; model dengan respons lebih lambat akan gagal dan dapat diuji kembali setelah kondisi provider diperiksa.

Tes ini membuktikan koneksi, pemanggilan tool, dan kontrak JSON pada fixture. Ranking personal, lamaran, pengiriman, dan hasil CV/PDF tetap memerlukan profil lengkap serta verifikasi tersendiri.

Kontrak integrasi diperiksa dari [9router](https://github.com/decolua/9router), [provider OpenCode](https://opencode.ai/docs/providers/#custom-provider), dan CLI OpenCode lokal versi 1.18.16. Implementasi autentikasi otomatis mengikuti client CLI yang dikirim bersama 9router versi 0.5.59.
