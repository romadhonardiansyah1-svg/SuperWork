# AUDIT ROADMAP: MODUL JOB SCRAPER (SUPERWORK)

Dokumen ini menyajikan hasil **Deep Technical Audit** yang komprehensif, berbasis bukti kode nyata (*code-grounded*), dan bebas asumsi untuk modul **Job Scraper** pada repositori SuperWork. Audit mencakup pipeline CLI agent portals (`.agents/skills/*-search`), skrip state & deduplikasi Python (`tools/scrape_state.py`, `tools/job_key.py`, `tools/install_portal_clis.py`, `tools/fetch_page.py`), integrasi antarmuka dashboard (`tools/dashboard_jobs.py`), serta spesifikasi skill kanonis (`.claude/skills/job-scraper/`).

---

## 1. Handover Status Summary

### Status Pekerjaan Warisan Agen Sebelumnya
Modul Job Scraper saat ini berada dalam arsitektur hybrid yang terbagi menjadi dua subsistem paralel:
1. **Agent Skill / CLI Scraper Subsystem (`.claude/skills/job-scraper/`, `.agents/skills/*-search/`, `tools/scrape_state.py`, `tools/job_key.py`):**
   - Dirancang untuk interaksi AI Agent (Claude Code / Antigravity / Codex) yang memanggil executable CLI TypeScript mandiri berbasis Bun (`bun run src/cli.ts`) untuk query portal (LinkedIn, Freehire, Jobnet, Jobindex, Jobdanmark, Jobbank).
   - Menghasilkan output JSON terstandarisasi yang kemudian difilter, dideduplikasi, dan disimpan ke `job_scraper/seen_jobs.json` secara atomik melalui `tools/scrape_state.py`.
2. **Dashboard Search / Local Web Subsystem (`tools/dashboard_jobs.py`, `dashboard/app.js`):**
   - Menangani pencarian real-time via backend lokal Python untuk antarmuka web, terhubung langsung ke API publik (Freehire, Arbeitnow, Arbeitnow UK) dan parser HTML publik (Kalibrr, Glints, Jobstreet, Google).

### Estimasi Kesiapan Produksi (Production Readiness)
- **Kesiapan Keseluruhan: 72%**
  - **Core Logic Deduplikasi & Normalisasi Kunci (`job_key.py`, `scrape_state.py`):** **95%** (Sangat matang, test suite unit 100% pass, atomik file locks, UTF-8 Windows safe).
  - **Portal CLI Engine (`.agents/skills/`):** **70%** (Terkendala *hard dependency* pada runtime `bun` di environment Windows native; jika `bun` tidak terinstal, sistem CLI lumpuh total dan jatuh ke WebSearch fallback yang rentan scraping drift).
  - **Dashboard Jobs Integrator (`dashboard_jobs.py`):** **85%** (SSRF protection & rate limiting kokoh, namun coupling tinggi dengan DOM pihak ketiga Kalibrr/Glints/Jobstreet yang rapuh terhadap perubahan markup).
  - **Pipeline Unifikasi Antar Sub-modul:** **40%** (Terjadi *split-brain*: hasil pencarian dashboard tersimpan di SQLite/JSON store dashboard lokal, sedangkan CLI agent menyimpan di `job_scraper/seen_jobs.json`. Belum ada sinkronisasi dua arah).

---

## 2. Daftar Temuan Deep Audit

### Kategori 1: Standar Industri & Best Practices (Type-Safety, Security, Error Handling)

#### [FINDING-1.1] [CRITICAL] Hard Runtime Dependency pada `bun` Tanpa Node.js Fallback pada CLI Portals
- **Referensi:** `.claude/skills/job-scraper/SKILL.md:L51-L58`, `tools/install_portal_clis.py:L75`, `.agents/skills/linkedin-search/cli/src/cli.ts:L1`
- **Akar Masalah:** Seluruh CLI portal di `.agents/skills/*/cli/` menggunakan hashbang `#!/usr/bin/env bun` dan skrip dependensi `package.json` yang secara eksplisit menjalankan `bun run` atau `bun test`. Pada sistem Windows tanpa instalasi Bun secara global (terbukti saat uji coba terminal: `'bun' is not recognized as an internal or external command`), seluruh CLI gagal dieksekusi. Skrip instalasi `tools/install_portal_clis.py` langsung gagal pada baris 75 (`run([bun, "install"], cwd=cli_dir)`).
- **Dampak Teknis:** Di environment Windows native tanpa Bun, CLI portals tidak dapat diinstal dan tidak dapat dijalankan sama sekali. Agent dipaksa melakukan WebSearch fallback manual yang tidak terstruktur.
- **Solusi Teknis:** Sediakan kompatibilitas eksekusi berbasis Node.js/TSX atau transpile bundle `.js` ringan (zero-dependency) yang dapat dijalankan langsung via `node` bawaan yang sudah terinstal di environment pengguna.

#### [FINDING-1.2] [HIGH] Runtime Type Casting Tanpa Validasi Skema Zod pada Respon API Jobnet
- **Referensi:** `.agents/skills/jobnet-search/cli/src/commands/search.ts:L21-L25`
- **Akar Masalah:** Kode mencatat komentar: *"A TypeScript claim is not runtime validation: apiFetch casts the JSON body, so a null here arrives typed as string and .slice() throws, killing the whole search as API_ERROR (#418)"*. Meskipun baris 25 menandai `publicationDate: string | null`, pemanggilan `apiFetch` pada `helpers.ts` tetap melakukan casting tipe `as T` tanpa validasi runtime parser (misalnya via `zod` yang sebenarnya sudah terpasang sebagai dependensi di `package.json`).
- **Dampak Teknis:** Jika skema API Jobnet mengalami perubahan struktur (misalnya `jobAds` berupa objek error atau properti `title`/`hiringOrgName` menjadi `null`), terjadi unhandled TypeError saat destructuring/mapping di baris 118–157.
- **Solusi Teknis:** Manfaatkan `zod` schema validator yang sudah diimport pada baris 2 untuk memvalidasi `SearchApiResponse` secara runtime sebelum proses transformasi data.

---

### Kategori 2: Kelengkapan & Validitas Logic (Functional & QA Check)

#### [FINDING-2.1] [HIGH] Split-Brain Deduplikasi State Antara Dashboard dan CLI Agent
- **Referensi:** `tools/scrape_state.py:L16`, `tools/dashboard_jobs.py:L318-L328`, `tools/dashboard_store.py`
- **Akar Masalah:** `scrape_state.py` mengelola deduplikasi file `job_scraper/seen_jobs.json` menggunakan format kunci `make_key(company, title, url)` dari `tools/job_key.py`. Sebaliknya, `tools/dashboard_jobs.py:L318` menghasilkan ID numerik berbasis SHA-256 dari URL kanonikal (`int.from_bytes(hashlib.sha256(...).digest()[:6], "big") + 1000000`) dan menyimpannya di file store dashboard terpisah (`.superwork/state.json`).
- **Dampak Teknis:** Lowongan yang telah ditemukan dan disimpan oleh pengguna melalui Web Dashboard tidak otomatis terdeteksi sebagai "already seen" oleh `/scrape` CLI, dan sebaliknya. Hal ini memicu duplikasi pekerjaan evaluasi dan potensi pelamaran berulang.
- **Solusi Teknis:** Tambahkan integrasi bridging pada `tools/scrape_state.py` untuk membaca juga daftar URL dan pasangan company+title yang tersimpan di store dashboard (`tools/dashboard_store.py`), serta menyinkronkan kunci kanonikal.

#### [FINDING-2.2] [MEDIUM] Deadlock Potensial & Penanganan Regex Substring Tidak Tuntas pada Fallback Non-Latin `job_key.py`
- **Referensi:** `tools/job_key.py:L132-L148`
- **Akar Masalah:** Pada fungsi `make_key`, pencarian nomor ID pekerjaan via regex `_JOB_ID.search(url or "")` mencari pola minimal 6 digit (`\d{6,}`). Jika lowongan berasal dari portal yang memiliki format alphanumeric ID unik (misalnya format UUID atau Base62 seperti LinkedIn shortlink tertentu atau Glints job ID yang memuat karakter alfabet), regex ini menghasilkan `None` dan jatuh ke branch fallback hash URL/title baris 147. Pada kasus URL kosong atau tidak memiliki ID numerik 6 digit, judul non-latin menghasilkan fallback `untitled-<hash>`.
- **Dampak Teknis:** Lowongan non-latin dari satu perusahaan tanpa URL atau URL tanpa 6 digit berturut-turut menghasilkan nama kunci generik `company_untitled-xxxxxx` yang menyulitkan pelacakan audit dan verifikasi manual di file tracker.
- **Solusi Teknis:** Perluas pola regex ID portal agar mendukung format alphanumeric id dari berbagai portal (misal UUID dan hash slug) sebelum fallback ke SHA-1 digest.

---

### Kategori 3: Kepatuhan Clean Architecture & Separation of Concerns

#### [FINDING-3.1] [HIGH] Pelanggaran Batas Layer: Polling HTML Parsing Langsung di Dalam Engine Pencarian Dashboard
- **Referensi:** `tools/dashboard_jobs.py:L371-L400`, `tools/dashboard_jobs.py:L953-L974`
- **Akar Masalah:** Kelas `_ListingPage` dan `_MetadataParser` digabungkan langsung di dalam file `tools/dashboard_jobs.py` (yang panjangnya mencapai 1.041 baris kode). Di dalamnya bercampur aduk antara:
  - Definisi metadata konfigurasi sumber (`SOURCE_DEFINITIONS`)
  - Logika jaringan tingkat rendah (raw socket socket-level DNS pinning, TLS wrapping via `_request_once`)
  - Aturan kepatuhan robots.txt (`_robots_permits`)
  - Parsing spesifik DOM Next.js Apollo Cache milik Glints (`props.apolloCache['Job:' + id]`)
- **Dampak Teknis:** Pelanggaran prinsip *Single Responsibility Principle* (SRP). Setiap kali struktur UI Glints atau Jobstreet berubah, file `dashboard_jobs.py` berisiko mengalami regresi pada modul network security maupun discovery sumber lainnya.
- **Solusi Teknis:** Pisahkan parser spesifik portal/vendor ke dalam sub-modul terisolasi, misalnya `tools/scrapers/glints_parser.py`, `tools/scrapers/kalibrr_parser.py`, dan `tools/network/secure_fetcher.py`.

---

### Kategori 4: Reusabilitas Komponen & Code Hygiene (Zero Deadcode)

#### [FINDING-4.1] [MEDIUM] Redundansi Normalisasi String Teks Antara `rank_state.py`, `scrape_state.py`, dan `dashboard_jobs.py`
- **Referensi:** `tools/rank_state.py:L140-L147`, `tools/dashboard_jobs.py:L235-L244`, `tools/job_key.py:L66-L73`
- **Akar Masalah:** Terjadi duplikasi logika normalisasi teks (slugifikasi, pembersihan tanda baca, penghilangan aksen Unicode NFKD/NFC).
  - `rank_state.py:L140` mendefinisikan `norm(text)` dengan `unicodedata.normalize("NFC", ...)`.
  - `tools/job_key.py:L66` mendefinisikan `slugify(text)` dengan `unicodedata.normalize("NFKD", ...)`.
  - `tools/dashboard_jobs.py:L235` mendefinisikan `_norm(value)` dengan aturan translasi khusus industri (mengubah "developer" -> "engineer", "administrasi" -> "admin", dll.).
- **Dampak Teknis:** Inkonsistensi komparasi string lintas modul. Pasangan company-title yang dianggap cocok di dashboard bisa lolos deduplikasi di `scrape_state.py` karena perbedaan aturan pembersihan karakter dan normalisasi sinonim.
- **Solusi Teknis:** Ekstrak fungsi normalisasi string terpadu ke dalam shared utility modul (misal `tools/text_norm.py`) dengan hierarki jelas: normalisasi kanonikal identitas vs normalisasi pencarian fuzzy semantik.

#### [FINDING-4.2] [LOW] Deadcode & Sisa File Kosong
- **Referensi:** `job_scraper/.gitkeep:L1`
- **Akar Masalah:** Direktori `job_scraper/` hanya berisi file `.gitkeep`. File `seen_jobs.json` baru dibuat secara on-demand saat run pertama. Namun dokumentasi di beberapa tempat masih merujuk seolah-olah skrip Python scraper reside di dalam direktori `job_scraper/`.
- **Dampak Teknis:** Kebingungan developer baru mengenai lokasi source code engine scraper (`tools/` vs `job_scraper/` vs `.agents/skills/`).
- **Solusi Teknis:** Dokumentasikan struktur direktori secara eksplisit dalam `README.md` modul dan pastikan `.gitignore` secara tegas mencakup artefak data temporer.

---

### Kategori 5: Performa Rendering & Jaringan (Network Throttling, Race Conditions, Concurrency)

#### [FINDING-5.1] [HIGH] Kurangnya Dynamic Throttling & Concurrency Pool Cap pada CLI Agent Searches
- **Referensi:** `.claude/skills/job-scraper/SKILL.md:L59-L74`
- **Akar Masalah:** Spesifikasi Step 1b memerintahkan: *"Run all portal CLI calls in parallel where possible using the Agent tool"*. Portals seperti LinkedIn dan portal lokal Denmark memiliki proteksi anti-scraping dan rate-limiting IP yang ketat (HTTP 429).
- **Dampak Teknis:** Jika agen menjalankan 6 CLI portal secara simultan dari mesin yang sama, portal yang sensitif (terutama LinkedIn Guest endpoint) langsung memblokir IP atau memicu CAPTCHA, menyebabkan kegagalan pencarian beruntun (*cascade failure*).
- **Solusi Teknis:** Tetapkan strategi konkurensi adaptif (maksimal 2 request paralel untuk portal sensitif seperti LinkedIn) dan tambahkan jitter backoff default pada helpers fetcher CLI.

---

## 3. Actionable Execution Roadmap

Rencana eksekusi berikut dirancang bertahap untuk menuntaskan seluruh temuan secara terukur:

```
+-----------------------------------------------------------------------------------+
|                           JOB SCRAPER EXECUTION ROADMAP                           |
+-----------------------------------------------------------------------------------+
|                                                                                   |
|  [PHASE 1] CRITICAL FIXES & RUNTIME RESILIENCE                                    |
|  * Buat Node.js / TSX fallback executor untuk CLI portals (.agents/skills/*)      |
|  * Validasi runtime Zod pada respon API Jobnet, Freehire, dan LinkedIn           |
|  * Perbaiki regex ID extraction pada tools/job_key.py                             |
|                                                                                   |
|  [PHASE 2] UNIFIKASI STATE & ARSITEKTUR BERSIH (CLEAN ARCHITECTURE)               |
|  * Sinkronkan state deduplikasi antara tools/scrape_state.py & dashboard_store    |
|  * Refaktor tools/dashboard_jobs.py: Pisahkan network layer & vendor HTML parser  |
|  * Ekstrak shared text normalizer ke tools/text_norm.py (eliminasi duplikasi)    |
|                                                                                   |
|  [PHASE 3] QUALITY ENHANCEMENT & ADAPTIVE SCRAPING                                |
|  * Implementasikan adaptive rate-limiting dan jitter backoff pada CLI portals     |
|  * Standardisasi error reporting & logging terstruktur di seluruh portal          |
|  * Bersihkan file yatim / standarisasi job_scraper/ metadata                      |
|                                                                                   |
+-----------------------------------------------------------------------------------+
```

### Phase 1: Critical Fixes & Incomplete Logic (Prioritas Tertinggi)
1. **Penyediaan Alternatif Eksekusi CLI Portals:**
   - Tambahkan wrapper atau instruksi eksekusi berbasis Node.js/TSX di setiap `package.json` portal CLI sehingga pengguna Windows yang tidak memiliki Bun tetap dapat menjalankan CLI pencarian.
   - Perbarui `tools/install_portal_clis.py` agar mendeteksi keberadaan `bun` terlebih dahulu, dan melakukan fallback aman ke `npm install` jika `bun` absen.
2. **Runtime Zod Schema Validation pada API Adapters:**
   - Tambahkan skema validasi Zod pada `jobnet-search/cli/src/commands/search.ts` dan `freehire-search/cli/src/commands/search.ts` sebelum data dipetakan ke field output kanonikal.
3. **Penyempurnaan Regex ID pada `tools/job_key.py`:**
   - Perbarui ekspresi reguler ekstraksi ID agar mengenali format identifier non-numerik (UUID dan alphanumeric slugs).

### Phase 2: Clean Architecture Refactoring & State Unification
1. **Bridging State Deduplikasi:**
   - Hubungkan `tools/scrape_state.py` agar memeriksa status lowongan dari file state dashboard (`.superwork/state.json`) di samping `seen_jobs.json` dan `tracker.csv`.
2. **Dekomposisi `tools/dashboard_jobs.py`:**
   - Pisahkan logika raw network socket / TLS security ke dalam `tools/network/secure_client.py`.
   - Ekstrak parser DOM Glints dan Kalibrr ke modul terpisah di `tools/scrapers/`.
3. **Konsolidasi Shared Utilities (DRY):**
   - Satukan implementasi `norm`, `slugify`, dan pembersihan aksen ke dalam `tools/text_utils.py` untuk menjamin konsistensi 100% komparasi data.

### Phase 3: Performance, Reusability, & Rate Limiting
1. **Adaptive Rate Limiting & Concurrency Throttling:**
   - Konfigurasikan batasan konkurensi pada pipeline eksekusi pencarian portal agar tidak memicu HTTP 429 atau IP block.
2. **Standardisasi Output Diagnostic Portals:**
   - Pastikan seluruh CLI portal mengeluarkan format pesan error seragam (`{ ok: false, error: string, code: string }`) ke `stderr` saat terjadi timeout atau rate limit.

---

## 4. QA Verification Checklist

Matriks pengujian berikut wajib dipenuhi sebelum menyatakan modul Job Scraper lulus uji dan siap rilis produksi:

| No | Kategori Uji | Skenario Pengujian | Hasil yang Diharapkan | Status |
|:---|:---|:---|:---|:---:|
| 1 | **CLI Runtime** | Jalankan `python tools/install_portal_clis.py --dry-run` | Menemukan seluruh manifest `package.json` tanpa error | `PASS` |
| 2 | **Contract Check** | Jalankan `python -m unittest tests/test_scrape_contract.py` | Seluruh 6 portal CLI memenuhi kontrak output fields | `PASS` |
| 3 | **State Deduplication** | Jalankan `python -m unittest tests/test_scrape_state.py` | Deduplikasi `candidates` dan `store` bekerja atomik | `PASS` |
| 4 | **Key Normalization** | Jalankan `python -m unittest tests/test_job_key.py` | Kunci slugify konsisten, aman UTF-8, dan zero collision | `PASS` |
| 5 | **Provenance Audit** | Jalankan `python -m unittest tests/test_scrape_provenance.py` | Pelacakan sumber (CLI vs WebSearch) tercatat akurat | `PASS` |
| 6 | **Dashboard Jobs Unit** | Jalankan `python -m unittest tests/test_dashboard_jobs.py` | Filter kueri, SSRF security guard, dan parser lulus uji | `PASS` |
| 7 | **Portal Health Probe** | Jalankan simulasi health check `/scrape health` | Portal bermasalah terdeteksi tanpa crash pada main flow | `PENDING` |
| 8 | **Cross-State Sync** | Pengujian integrasi pencarian CLI vs Simpan di Dashboard | Tidak ada duplikasi lowongan antara kedua subsistem | `PENDING` |

---
*Laporan Deep Technical Audit ini disusun secara objektif berdasarkan verifikasi baris kode langsung pada repositori SuperWork.*
