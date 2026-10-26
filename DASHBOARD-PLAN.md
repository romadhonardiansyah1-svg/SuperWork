# SuperWork lokal: rencana implementasi dan kriteria selesai

Status: selesai dan terverifikasi, 24 Oktober 2026. Data kandidat kosong; pengujian memakai fixture yang sudah dicadangkan.

## Tujuan dan batas

Satu shortcut Windows membuka dashboard lokal tanpa terminal. Dashboard mengelola model aktual 9router, pengujian tool calling/JSON, profil dari CV unggahan, pencarian dengan filter opsional, sumber/bukti lowongan, lamaran, dokumen, persiapan, dan asisten. Layanan lokal hanya mendengarkan loopback. Keberhasilan fixture tidak sama dengan keberhasilan semua provider atau cakupan seluruh situs lowongan.

Gunakan Python 3.11 yang tersedia, HTML/CSS/JavaScript asli, dan komponen/tokens NeedMCP Alabaster Grid Console. Reuse tools/superwork.py dan workflow kanonis .claude/. Tidak ada Electron, layanan cloud baru, auto-submit lamaran, atau pembacaan .env. API key NeedMCP tidak disimpan dalam proyek atau browser.

## Urutan kerja

- [x] 1. Fondasi: kontrak API lokal, penyimpanan atomik, proteksi Host/Origin, unggahan terbatas, dan uji regresi.
- [x] 2. 9router: daftar model asli, pilihan persisten, lifecycle aman, status/error, dan uji nyata memakai fixture acak.
- [x] 3. Dashboard: reuse 10 halaman, terapkan NeedMCP, hubungkan semua tindakan nyata; buang status koneksi/pemeriksaan palsu.
- [x] 4. Alur harian: profil/CV, pencarian uji dan langsung terpisah, simpan/lamaran, dokumen, wawancara, belajar, asisten, ekspor/impor cadangan.
- [x] 5. Windows: launcher tersembunyi, reuse instance, pemeriksaan kesiapan, shortcut Desktop, dan penutupan eksplisit.
- [x] 6. Verifikasi: unit/integrasi, suite lama, lint yang tersedia, syntax/build, browser desktop/mobile, lifecycle, shortcut, dan bukti inferensi nyata.

## Kontrak untuk implementasi paralel

Semua API JSON. Bentuk error: {ok:false,error:"pesan aman"}. Browser mengambil token sesi dari GET /api/bootstrap; semua mutasi mengirim X-SuperWork-Token, Origin sama, dan application/json. Tidak ada endpoint eksekusi shell bebas.

- GET /api/bootstrap -> {ok,version,token,state,router,models,capabilities}; GET /api/health -> {ok,app:"SuperWork",version}.
- GET /api/router -> {ok,router}; router = {status:"ready|stopped|starting|error",managed:boolean,can_stop:boolean,base_url,message}.
- POST /api/router/start|stop|restart -> {ok,router}; tidak membunuh proses eksternal. GET /api/models -> {ok,models:[{id}],router}; GET /api/router/logs -> {ok,lines:[string]}.
- PUT /api/state body {state:object} -> {ok,state}; state mengikuti struktur preview v2, bertambah searchMode, liveJobs, settings. Rahasia tidak disimpan di state.
- POST /api/check body {model:string} -> {ok,model,tool_calling,json_output,output,checked_at,fixture_only:true}; kegagalan tidak pernah dianggap PASS.
- POST /api/ai body {model,task:"fit|document|interview|learning|chat|profile",context:object,message?:string} -> {ok,model,task,output:object,tool_calling:true,json_output:true}. Input terbatas, tool hanya read_context, tidak ada akses file/shell oleh model.
- POST /api/search body {query,filters?:object,sources?:[string],boards?:[{type:"greenhouse|lever",slug:string}]} -> {ok,jobs:[job],sources:[status],searched_at}; global berarti tidak ada wilayah wajib, cakupan dinyatakan per sumber. Data uji tetap dari katalog fixture UI.
- POST /api/verify body {url:string} -> {ok,evidence:object}; hanya tujuan publik, redirect dicek, HTTP 200 bukan bukti tidak palsu.
- POST /api/files body {name,content_base64} -> {ok,file:{id,name,size,type,text,extraction_status}}; GET /api/files/{id} mengunduh asli; DELETE /api/files/{id}.
- GET /api/backup -> JSON snapshot termasuk CV; POST /api/restore body snapshot -> {ok,state}; POST /api/shutdown -> {ok}, menutup hanya layanan milik aplikasi.

Job mempertahankan bentuk preview: id angka stabil, title, company, country, region, city, mode, employment, level, sector, skills[], description, sourceType, sourceName, sourceUrl, foundOn, origin, status, checkedAt, postedDays, eligible[], mock:false; field evidence menyimpan waktu/URL/status faktual. Nilai yang tidak diketahui diberi label belum diketahui.

## Pembagian berkas

- Pengendali router/Windows: tools/router_runtime.py, tools/launch_dashboard.py, tools/install_dashboard_shortcut.ps1, Start-SuperWork.vbs dan tests/test_router_runtime.py, tests/test_dashboard_launcher.py.
- Frontend: dashboard/index.html, dashboard/app.js, dashboard/styles.css, dashboard/logic.test.cjs. Preview awal tetap sebagai backup.
- Pencarian: tools/dashboard_jobs.py, tests/test_dashboard_jobs.py. Gunakan API publik yang terdokumentasi, cakupan jelas, sumber perusahaan pilihan pengguna; kegagalan per sumber tidak menggagalkan sumber lain.
- Integrasi utama: tools/dashboard_server.py, tools/dashboard_ai.py, tools/dashboard_store.py, tes masing-masing, .needmcp/state.json, dokumentasi dan bukti hasil.

## Perintah validasi

python -m unittest discover -s tests -t . -v

python tools/lint_skills.py

python tools/security_guards.py

python -m compileall -q tools

node --check dashboard/app.js

node --test dashboard/logic.test.cjs

python tools/dashboard_server.py --port 4175

Tidak ada bundler/lint/typechecker frontend yang dikonfigurasi; syntax dan browser nyata menjadi pemeriksaan frontend. Tambahkan typecheck CLI portal hanya bila modulnya diubah. Periksa ulang diff/daftar berkas dan lakukan review keamanan serta penyederhanaan sebelum menyatakan selesai.

## Kriteria penerimaan

Model baru dari router muncul setelah refresh tanpa mengubah pilihan diam-diam. Router berhenti tidak mematikan dashboard. Proses yang dimulai pihak lain tidak dihentikan. Shortcut tidak menampilkan terminal dan tidak membuat instance ganda. CV/dokumen bertahan setelah restart dan bisa dicadangkan. Semua tombol punya tindakan yang benar atau status tidak tersedia dengan alasan jelas. Uji nyata harus membuktikan pemanggilan tool dan JSON yang sesuai nilai fixture acak, bukan jawaban hafalan. Tidak ada klaim pengalaman kandidat buatan, lowongan terverifikasi palsu, atau integrasi Gmail/Notion pura-pura.

## Hasil dan pemakaian

Panduan harian: [DASHBOARD.md](DASHBOARD.md). Bukti uji, batas verifikasi, dan screenshot: [.artifacts/dashboard-operational-2026-10-24/HASIL.md](.artifacts/dashboard-operational-2026-10-24/HASIL.md).

Hasil akhir: 936 tes Python pada snapshot sumber terbaru, 78 tes backend terfokus, 50 tes Node, dan 50 pemeriksaan browser responsif lulus. Pemeriksaan model nyata, penutupan/pembukaan melalui shortcut, dan pemulihan CV juga lulus. Cakupan sumber serta integrasi manual dinyatakan di dashboard dan panduan.

## Revisi 25 Oktober 2026: PDF, latihan, sumber

Kontrak dan urutan implementasi: [.artifacts/dashboard-pdf-prep-sources-2026-10-25/PLAN.md](.artifacts/dashboard-pdf-prep-sources-2026-10-25/PLAN.md).

- PDF langsung untuk dokumen tersimpan melalui endpoint POST ber-ID, tanpa dialog cetak atau layanan eksternal. ReportLab dalam runtime `.venv` proyek; font Lato yang sudah ada; catatan tinjauan tetap di dashboard.
- Wawancara 12 pertanyaan unik per tahap, petunjuk, jawaban tersimpan, serta evaluasi terstruktur. Belajar 6 latihan dengan evaluasi hasil. Kontrak tool/JSON tetap ketat dan fakta kandidat tidak boleh dibuat-buat.
- Pengaturan sumber publik aktif/nonaktif, daftar board perusahaan maksimal enam, tambah/hapus, penjelasan cakupan dan asal. Pencarian memakai pilihan tersimpan dan filter wilayah opsional.
- Pengujian beberapa tab membuktikan snapshot lama dapat menimpa pencarian/jawaban terbaru. Perbaikan memakai revision SHA256 dan If-Match pada penyimpanan/pemulihan; konflik ditolak sebelum berkas atau state berubah. Snapshot awal aplikasi utama dipulihkan dan dibandingkan sebelum pembukaan hasil.
- Tes data terpisah pada port 4176, ekspor/render PDF, evaluasi model nyata, persistensi, review, dan verifikasi suite. Hasil terkini dan batas bukti dicatat pada folder artefak revisi tersebut; angka hasil awal di atas adalah riwayat implementasi sebelumnya.

## Revisi sumber Indonesia

Kontrak dan urutan: [.artifacts/dashboard-indonesia-sources-2026-10-25/PLAN.md](.artifacts/dashboard-indonesia-sources-2026-10-25/PLAN.md).

- Kalibrr.id: pembacaan halaman pencarian publik pertama, lintas bidang/magang, lokasi dan tenggat sumber, mengikuti robots.txt serta batas jaringan yang sudah ada.
- Jobstreet dan Glints: pencarian di portal resmi karena pembatasan agregasi otomatis; URL lowongan dapat dicatat ke Tersimpan dengan nama sumber yang dikenali. Tidak ada jumlah hasil atau waktu cek otomatis palsu.
- Pemeriksaan pengguna menyimpan status, waktu dan catatan bukti terpisah dari pemeriksaan akses otomatis. Hasil tersimpan yang melewati tenggat tetap disaring. Pilihan sumber lama dipertahankan, sumber baru diaktifkan lewat Pengaturan.
- QA memakai data terpisah; data CV/dokumen/lamaran utama dipertahankan. Bukti terkini dicatat di folder artefak revisi sumber Indonesia.
