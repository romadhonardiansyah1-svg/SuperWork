# Menggunakan SuperWork lokal

Klik dua kali shortcut **SuperWork** di Desktop. Launcher menyalakan dashboard dan 9router bila diperlukan, menunggu layanan, lalu membuka **http://127.0.0.1:4175/** di browser bawaan. Tidak memerlukan terminal atau administrator. Jika shortcut tidak tersedia, klik dua kali `Start-SuperWork.vbs` di folder proyek.

## Alur sehari-hari

1. **Pengaturan:** pilih model asli dari 9router, lalu jalankan **Periksa model**. Daftar dimuat ulang setiap 30 detik selama halaman aktif; model baru perlu pemeriksaannya sendiri. Pilihan yang hilang tidak diganti diam-diam. Kelola login provider melalui **Kelola provider**. Router lokal masih dapat memakai provider internet.
2. **Profil & CV:** unggah PDF/DOCX/TXT (maksimum 8 MB), tinjau teks, perbaiki profil, dan konfirmasi. PDF hasil pindai/terkunci dapat memerlukan TXT atau pengisian manual. Unggahan asli tetap terpisah dari dokumen lamaran. Jika profil contoh dari sesi sebelumnya masih tersimpan, unggah CV Anda sebelum memakai dokumen untuk melamar.
3. **Cari lowongan:** ketik kata kunci lalu **Cari**. Hasil sumber aktif masuk bertahap ke satu daftar; halaman berikutnya dibaca otomatis selama tersedia. **Filter** membuka pilihan negara, wilayah/kota, jenis pekerjaan (termasuk **Magang**), status, dan sumber. Semua opsional. **Jeda pencarian** menyelesaikan halaman berjalan lalu menyimpan posisi; **Lanjutkan pencarian** meneruskan, termasuk setelah aplikasi dibuka ulang. **Tampilkan hasil tersimpan** hanya menambah kartu yang ditampilkan. Lihat asal, waktu pemeriksaan, dan bukti setiap lowongan. HTTP 200 bukan jaminan perusahaan asli atau lowongan masih terbuka.
4. **Lamaran dan persiapan:** simpan lowongan, catat tahap/tanggal sebenarnya, pilih posisi aktif, siapkan CV/surat, dan tinjau sebelum menandainya digunakan. Tahap **Belum dikirim** berarti catatan belum memiliki pengiriman ke perusahaan. Wawancara menggunakan posisi dan dokumen tersebut. Rencana belajar mengikuti informasi keterampilan yang belum tercatat. Asisten memakai konteks yang dipilih. Lamaran dikirim sendiri melalui situs perusahaan/portal.
5. **Cadangan dan penutupan:** unduh cadangan lengkap di Pengaturan; cadangan mencakup CV asli. Pemulihan meminta konfirmasi dan memvalidasi berkas sebelum mengganti data aktif. Menutup tab membiarkan layanan hidup. **Tutup SuperWork** menghentikan aplikasi dan router miliknya; shortcut membuka keduanya lagi.

## Cakupan yang tersedia

- Sebelas halaman: Beranda, Profil & CV, Cari lowongan, Lamaran saya, Dokumen, Wawancara, Rencana belajar, Asisten AI, Aktivitas, Integrasi portal, Pengaturan.
- Sumber gabungan: Freehire, Arbeitnow, Arbeitnow UK, Kalibrr, Jobstreet, Glints, Google, Google · Karier perusahaan, serta board Greenhouse/Lever perusahaan yang ditambahkan sendiri. Sumber yang gagal ditampilkan terpisah tanpa menghentikan sumber lainnya. Akses Google atau portal dapat dibatasi situs tujuan; ini tidak mencakup seluruh situs internet.
- Sumber perusahaan ditambahkan di Pengaturan menggunakan format `greenhouse:slug` atau `lever:slug`, sesuai halaman karier resmi. Data sumber yang tidak disebutkan tetap diberi label belum diketahui.
- Login akun portal dilakukan sendiri di browser lokal melalui Integrasi portal. Pengiriman lamaran, Gmail dan Notion tetap manual.
- Dokumen AI perlu ditinjau sebelum digunakan. **Unduh PDF** pada daftar dokumen atau pratinjau menghasilkan lampiran A4 langsung, tanpa dialog cetak Windows dan tanpa layanan luar. Font Lato, pembungkusan teks, halaman lanjutan, dan nomor halaman ditangani lokal. Catatan tinjauan tetap terlihat di dashboard dan tidak ikut PDF lamaran. HTML serta cetak browser tetap tersedia. Fitur CLI LaTeX lama tetap terpisah.
- Wawancara menghasilkan **12 pertanyaan per tahap** (empat tahap), dengan fokus dan petunjuk susunan jawaban. Setiap jawaban mendapat evaluasi kekuatan, kekurangan, saran, dan tindak lanjut. Jawaban serta evaluasi disimpan per posisi, tahap, dan versi dokumen; mengubah jawaban menandai evaluasi sebelumnya perlu diperbarui. Rencana belajar menghasilkan **6 latihan**, masing-masing dapat ditulis hasilnya dan dievaluasi. Riwayat jawaban dibatasi 60 entri terbaru agar penyimpanan tetap terkendali.

## Mengelola sumber lowongan

Di **Pengaturan → Sumber pencarian**, aktifkan/matikan sumber publik. Tambahkan board perusahaan melalui pilihan platform dan slug, lalu gunakan checkbox atau **Hapus** untuk mengurangi sumber. Pilihan tersimpan dan berlaku pada pencarian berikutnya. Hasil dan lamaran yang sudah disimpan tetap dapat ditinjau.

| Sumber | Cakupan yang digunakan aplikasi | Cara mengelola |
| --- | --- | --- |
| [Freehire](https://freehire.me/docs/api) | Agregator teknologi/engineering lintas negara; pencarian API dengan kata kunci dan filter. | Checkbox aktif/nonaktif. |
| [Arbeitnow](https://www.arbeitnow.com/blog/job-board-api) | Halaman terbaru lintas bidang di Eropa, terutama Jerman. | Checkbox aktif/nonaktif. |
| Arbeitnow UK | Halaman terbaru untuk pasar Inggris. | Checkbox aktif/nonaktif. |
| [Kalibrr Indonesia](https://www.kalibrr.id/home) | Pencarian publik lintas bidang termasuk magang; berlanjut jika halaman menyediakan tautan berikutnya. | Checkbox di Filter atau Pengaturan. |
| [Jobstreet Indonesia](https://id.jobstreet.com/id/jobs) | Pencarian Indonesia lintas bidang; mengikuti tombol Next portal. | Profil browser lokal melalui Integrasi portal; checkbox mengatur pencarian gabungan. |
| [Glints](https://glints.com/id/explore) | Pencarian Indonesia lintas bidang; memuat kartu berikutnya melalui gulir halaman. | Profil browser lokal melalui Integrasi portal. Gerbang login menghasilkan peringatan dan kelanjutan yang bisa dicoba ulang. |
| Google | Menemukan halaman dari portal dan penerbit lain. | Checkbox; bila dibatasi, buka pencarian manual atau coba ulang setelah kendala selesai. |
| Google · Karier perusahaan | Pencarian berfokus pada alamat karier dan board perusahaan. | Checkbox; label situs perusahaan memerlukan bukti dari halaman, bukan sekadar kemunculan di Google. |
| [Greenhouse](https://docs.greenhouse.io/job-board.html) | Lowongan terbit pada board perusahaan yang ditambahkan. | Pilih platform, masukkan slug dari halaman karier resmi. |
| [Lever](https://github.com/lever/postings-api) | Lowongan terbit pada board perusahaan yang ditambahkan. | Pilih platform, masukkan slug dari halaman karier resmi. |

Maksimal 6 board perusahaan yang dikonfigurasi. Tidak ada pemotongan total 20 hasil per sumber atau 500 posting per board. Freehire dan Arbeitnow mengikuti kelanjutan API, Lever memakai pagination, dan Greenhouse membaca daftar board. Batas dari situs tujuan tetap berlaku; Freehire membatasi rentang API hingga 10.000 dan aplikasi menampilkan peringatan saat batas itu tercapai. Ukuran satu respons tetap dibatasi untuk menjaga aplikasi; penyimpanan keadaan maksimum 50 MB dan tidak membuang hasil diam-diam. Simpanan, CV, dan lamaran tidak dihapus ketika sumber gagal.

Kalibrr Indonesia memakai domain **kalibrr.id**. Pembaca mengikuti tautan berikutnya yang benar-benar tersedia, robots.txt, dan batas jaringan. Tenggat yang sudah lewat disaring dari hasil aktif, termasuk ketika hasil tersimpan dibuka kembali. Respons halaman atau label perusahaan di portal tidak dianggap bukti keaslian maupun jaminan masih menerima lamaran.

Jobstreet dan Glints dibaca melalui browser lokal, bukan API resmi. Login akun tidak menjamin akses selalu tersedia. Filter negara yang hanya memilih di luar Indonesia melewati kedua portal Indonesia ini; sumber global lainnya tetap dicari. Filter lain diterapkan pada setiap halaman. Pembatasan akses atau perubahan halaman ditampilkan sebagai kendala; **Coba ulang sumber** mempertahankan posisi terakhir.

Google menyimpan profil pencarian terpisah di `.superwork/search-browser/`. Tidak memerlukan akun Google untuk memulai. Halaman tanpa metadata posisi yang jelas diberi label **Hasil web** dan bisa dibuka atau disimpan sebagai tautan. Hasil dengan metadata JobPosting menampilkan perusahaan dan tenggat dari halaman, tetapi status penerimaan serta identitas tetap belum terkonfirmasi. Halaman perantara tetap dilabeli domain penerbitnya. Tidak ada penyelesaian CAPTCHA otomatis atau penyamaran browser.

## Integrasi Glints dan Jobstreet

1. Buka **Integrasi portal**. Atur kata kunci (default magang), lalu klik **Buka browser untuk login** pada portal pilihan. Microsoft Edge memakai profil terpisah untuk tiap portal, tanpa port debugging jaringan. Profil Chrome/Edge pribadi tidak dipakai.
2. Login atau daftar sendiri di situs resmi jika diperlukan. Jendela login memakai Edge biasa tanpa kontrol Playwright; selesaikan Google/MFA/CAPTCHA sendiri, lalu **tutup jendela portal tersebut**. Profil tetap tersimpan. SuperWork tidak memiliki kolom password dan tidak mengekspor cookie atau token akun.
3. Klik **Periksa akses** setelah jendela login ditutup. Pembaca Playwright memakai profil yang sama; selama login manual masih terbuka, pemeriksaan menunggu tindakan pengguna tanpa mengganggu formulir. Status login, keberadaan profil, browser terbuka dan keberhasilan membaca pencarian ditampilkan terpisah. Profil tersimpan tidak membuktikan akun sedang login. Status login dapat belum pasti bila portal tidak menampilkan kontrol akun yang jelas.
4. Klik **Sinkronkan lowongan** untuk mengambil halaman awal beserta pemeriksaan detail ke Cari lowongan. Untuk hasil halaman berikutnya, gunakan **Cari** pada menu Cari lowongan. Hasil sumber lain, simpanan, CV, lamaran dan dokumen dipertahankan. Pemeriksaan Glints dapat mencoba HTML publik bila belum memiliki profil; pencarian gabungan memakai browser lokal.
5. Menonaktifkan sumber hanya mengeluarkannya dari pencarian. **Hapus sesi lokal** meminta konfirmasi; tutup dahulu jendela login manual. Tindakan ini menutup pembaca dan menghapus hanya profil khusus portal itu. Lowongan tersimpan tidak dihapus. Menutup SuperWork menutup pembaca tetapi mempertahankan profil. Jendela login manual tetap menjadi kendali pengguna.

Status akun diambil dari kontrol akun portal: avatar profil Glints atau menu keluar Jobstreet, bukan hanya keberadaan folder profil. Hasil pemeriksaan beserta waktunya disimpan setelah restart. Saat menu Integrasi dibuka, sesi tersimpan yang belum diperiksa atau berusia lebih dari 30 menit diperiksa kembali; **Muat ulang status** memaksa pemeriksaan kedua portal. Jendela login manual yang masih terbuka tidak diganggu. Pemeriksaan dan sinkronisasi juga memperbarui status akun; kegagalan membuat status belum pasti, bukan mempertahankan tanda login berhasil yang sudah usang. Pembaca menggunakan Edge berjendela karena akses tanpa jendela dapat dibatasi portal.

Filter awal **Semua kecuali ditutup / kedaluwarsa** menampilkan hasil terbuka dan belum pasti. **Terbuka saat dicek** hanya menampilkan `open`. Pencarian gabungan membaca kartu secara bertahap; status yang belum diperiksa di detail tetap `unknown`. Pemeriksaan/sinkronisasi pada Integrasi membaca detail dengan batas waktu: status sumber, tenggat, pemberitahuan penutupan, atau tombol lamaran aktif dengan judul yang cocok. HTTP 200 saja tidak membuktikan masih menerima lamaran. Posisi yang disimpan atau dipakai dalam lamaran/dokumen dipertahankan. Status terbuka tidak mengesahkan identitas perusahaan.

Data profil browser berada di `.superwork/portal-browser/{glints,jobstreet}/`; metadata pemeriksaan nonrahasia ada di `portal-integrations.json`. Keduanya tidak termasuk cadangan dashboard. Jangan bagikan folder profil browser. Sesi dapat berakhir menurut kebijakan portal; buka browser dan login kembali bila diminta. HTTP gagal, perubahan struktur halaman, CAPTCHA, atau hasil yang tidak dapat dibaca dilaporkan sebagai kendala dan tidak menghapus katalog lama. Tidak ada stealth, pengulangan agresif, proxy, atau penyelesaian CAPTCHA otomatis.

Pilih **Catat lowongan portal**, salin tautan detail posisi, isi perusahaan, lokasi, bidang dan jenis pekerjaan (termasuk Magang). Lowongan masuk Tersimpan tanpa membuat atau mengirim lamaran. Nama portal dikenali dari domain resmi dan tautan pelacakan dibuang. Status awal belum dapat dipastikan, waktu cek Belum pernah diperiksa. Dari **Lihat bukti → Catat pemeriksaan saya**, buka sumber, pilih status yang terlihat, tulis bukti dan konfirmasi. Dashboard menampilkan Diperiksa pengguna dengan waktunya. Catatan ini dipertahankan setelah restart dan tidak mengesahkan identitas perusahaan.

Untuk ruang kerja baru, delapan sumber tersedia sebagai pilihan awal. Pilihan khusus yang sebelumnya dinonaktifkan pengguna dipertahankan; pilihan bawaan lama mendapat tambahan dua pencarian Google. Filter dan kelanjutan halaman tersimpan bersama data dashboard.

## Penyimpanan dan pemulihan

Data berada di `.superwork/`, diabaikan oleh Git: `state.json`, salinan sebelumnya `state.json.bak`, dan `files/` untuk CV. Batas state 50 MB; total unggahan 40 MB. Hapus versi dokumen yang tidak diperlukan setelah mencadangkan. Riwayat kegiatan dan chat dibatasi agar tidak tumbuh tanpa kendali. Catatan contoh lama tetap tersimpan bila masih dirujuk, tetapi katalog fiktif tidak muncul dalam pencarian harian.

Jika membuka beberapa tab, penyimpanan memakai pemeriksaan versi data. Tab yang memuat versi lama tidak dapat menimpa perubahan dari tab lain; pesannya **Belum tersimpan**. Salin dahulu isian baru yang masih dibutuhkan, lalu muat ulang tab untuk mengambil data terbaru. Pemeriksaan yang sama berlaku saat memulihkan cadangan dan menghapus salinan CV. Penghapusan memperbarui referensi CV dan berkasnya dalam satu operasi; konflik tidak menghapus berkas.

Konteks teks yang dipilih dikirim ke provider hanya saat menjalankan tugas AI. API key 9router dikelola oleh backend dan klien resmi 9router; tidak masuk HTML/localStorage/ekspor state. Key NeedMCP digunakan untuk desain dan tidak disertakan dalam aplikasi.

Jika model gagal, kuota habis atau jaringan putus, dashboard menampilkan kegagalan dan tidak menganggapnya lulus. Gunakan **Log**, **Muat ulang model**, atau pilih model lain sendiri. Router yang dimulai aplikasi lain ditampilkan sebagai eksternal dan tidak dihentikan SuperWork. Pengaturan provider tidak dihapus saat router dimatikan.

Jika browser menunjukkan sesi tidak sesuai setelah restart server, muat ulang halaman. Jika port 4175 dipakai aplikasi lain, launcher menampilkan pesan dan mempertahankan proses tersebut. Jika data rusak, jangan menimpa data: pulihkan cadangan atau gunakan `state.json.bak` dengan aplikasi ditutup.

## Implementasi dan pemeriksaan

Rencana/kontrak: `DASHBOARD-PLAN.md` dan `.artifacts/dashboard-integrations-2026-10-25/PLAN.md`. Backend HTTP memakai pustaka standar Python; ekstraksi PDF memakai pypdf, ekspor PDF memakai ReportLab, dan browser portal memakai Playwright Python 1.60.0 dengan Microsoft Edge terpasang. Dependensi dashboard dipatok di `requirements-dashboard.txt`. Frontend tidak menambah dependensi atau CDN dan memakai token desain NeedMCP Alabaster Grid Console. Shortcut mendahulukan `.venv/Scripts/pythonw.exe` ketika tersedia.

Untuk instalasi baru, siapkan runtime proyek sekali sebelum memakai shortcut:

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-dashboard.txt
```

Perintah pengembang:

```powershell
.venv/Scripts/python.exe -m unittest discover -s tests -t . -v
.venv/Scripts/python.exe tools/lint_skills.py
.venv/Scripts/python.exe tools/security_guards.py
.venv/Scripts/python.exe -m compileall -q tools
node --check dashboard/app.js
node --test dashboard/logic.test.cjs
```

Tes lama `test_reset_command` memerlukan checkout Git. Folder sumber saat ini adalah arsip tanpa `.git`; suite lengkap juga diuji dalam snapshot Git terisolasi tanpa mengubah folder utama. Hasil operasional awal dicatat di `.artifacts/dashboard-operational-2026-10-24/`; pemeriksaan magang dan tampilan harian terbaru berada di `.artifacts/dashboard-production-2026-10-24/HASIL.md`.
