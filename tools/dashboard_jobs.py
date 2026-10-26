"""Bounded public job search and conservative source checks for the local UI.

Public APIs are discovery evidence only. HTTP success and JobPosting metadata
never establish employer identity or guarantee that applications remain open.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time as day_time, timezone
import hashlib
import html
from html.parser import HTMLParser
import http.client
import ipaddress
import json
import re
import socket
import ssl
import time
import unicodedata
from urllib.parse import parse_qsl, quote, unquote, urlencode, urljoin, urlsplit, urlunsplit

try:
    from .page_decode import decode_page
    from .robots_check import allowed, has_undecodable_rule, is_robots_body
except ImportError:  # direct dashboard_server.py execution
    from page_decode import decode_page
    from robots_check import allowed, has_undecodable_rule, is_robots_body


SOURCE_DEFINITIONS = [
    {"id": "freehire", "name": "Freehire", "scope": "Teknologi dan engineering, beberapa negara; indeks agregator.",
     "docs": "https://freehire.me/docs/api"},
    {"id": "arbeitnow", "name": "Arbeitnow", "scope": "Lowongan terbaru lintas bidang di Eropa, terutama Jerman; halaman berikutnya mengikuti tautan sumber.",
     "docs": "https://www.arbeitnow.com/blog/job-board-api"},
    {"id": "arbeitnow-uk", "name": "Arbeitnow UK", "scope": "Lowongan terbaru lintas bidang untuk pasar Inggris; halaman berikutnya mengikuti tautan sumber.",
     "docs": "https://www.arbeitnow.com/blog/job-board-api"},
    {"id": "kalibrr", "name": "Kalibrr", "scope": "Pencarian publik Indonesia, termasuk magang; dilanjutkan bila sumber menyediakan tautan berikutnya.",
     "docs": "https://www.kalibrr.id/home", "mode": "public_page"},
    {"id": "jobstreet", "name": "Jobstreet", "scope": "Portal Indonesia, lintas bidang termasuk magang; halaman berikutnya melalui browser lokal.",
     "docs": "https://id.jobstreet.com/terms/en", "home": "https://id.jobstreet.com/id/jobs", "mode": "portal"},
    {"id": "glints", "name": "Glints", "scope": "Portal Indonesia, lintas bidang termasuk magang; hasil berikutnya melalui browser lokal.",
     "docs": "https://assets.glints.com/docs/GlintsTermsAndConditions.pdf", "home": "https://glints.com/id/explore", "mode": "portal"},
    {"id": "google", "name": "Google", "scope": "Penemuan halaman lowongan di web; penerbit dan status diperiksa terpisah dari mesin pencari.",
     "docs": "https://www.google.com/search/howsearchworks/", "home": "https://www.google.com/", "mode": "web", "sourceType": "web"},
    {"id": "google-careers", "name": "Google · Karier perusahaan", "scope": "Penemuan halaman karier dan board perusahaan melalui Google; identitas penerbit tetap perlu diperiksa.",
     "docs": "https://www.google.com/search/howsearchworks/", "home": "https://www.google.com/", "mode": "web", "sourceType": "web"},
]
DEFAULT_SOURCES = tuple(source["id"] for source in SOURCE_DEFINITIONS)
RESULT_LIMIT = 20  # Freehire request page size, never an accumulated-result ceiling.
MAX_PAGE_ROWS = 5000
MAX_RESPONSE_BYTES = 6 * 1024 * 1024
REQUEST_TIMEOUT = 10
USER_AGENT = "SuperWork/1.0 (personal local job dashboard)"
UNKNOWN = "Belum diketahui"
_INTERNSHIP = re.compile(r"\b(?:internships?|interns?|magang|praktikum)\b", re.I)
_SENSITIVE_QUERY = re.compile(r"(?:^|[_-])(token|key|secret|password|passwd|authorization|auth|signature|session)(?:$|[_-])", re.I)
_COUNTRIES = {
    "ID": ("Indonesia",), "DE": ("Jerman", "Germany", "Deutschland"),
    "GB": ("Britania Raya", "United Kingdom", "UK", "Great Britain"),
    "US": ("Amerika Serikat", "United States", "USA", "United States of America"),
    "SG": ("Singapura", "Singapore"), "NL": ("Belanda", "Netherlands"),
    "DK": ("Denmark",), "JP": ("Jepang", "Japan"), "CA": ("Kanada", "Canada"),
    "AU": ("Australia",), "MY": ("Malaysia",), "IN": ("India",), "FR": ("Prancis", "France"),
    "ES": ("Spanyol", "Spain"), "IT": ("Italia", "Italy"), "CH": ("Swiss", "Switzerland"),
    "AT": ("Austria",), "SE": ("Swedia", "Sweden"), "NO": ("Norwegia", "Norway"),
    "FI": ("Finlandia", "Finland"), "IE": ("Irlandia", "Ireland"), "PL": ("Polandia", "Poland"),
    "PT": ("Portugal",), "BR": ("Brasil", "Brazil"), "NZ": ("Selandia Baru", "New Zealand"),
    "PH": ("Filipina", "Philippines"), "VN": ("Vietnam",), "TH": ("Thailand",),
    "AE": ("Uni Emirat Arab", "United Arab Emirates"), "CN": ("Tiongkok", "China"),
    "KR": ("Korea Selatan", "South Korea"), "ZA": ("Afrika Selatan", "South Africa"),
}


class FetchError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _stamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _public_ip(value):
    address = ipaddress.ip_address(value)
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return address.is_global and not address.is_multicast and not getattr(address, "is_site_local", False)


def _validate_url(value):
    if not isinstance(value, str) or not value or len(value) > 2048 or re.search(r"[\s\\\x00-\x1f\x7f]", value):
        raise FetchError("invalid_url", "URL HTTP(S) publik tidak valid.")
    try:
        parts = urlsplit(value)
        host = (parts.hostname or "").encode("idna").decode("ascii").lower().rstrip(".")
        port = parts.port
        if parts.scheme not in ("http", "https") or not host or parts.username is not None or parts.password is not None:
            raise ValueError()
        if port not in (None, 80, 443) or host == "localhost" or host.endswith((".localhost", ".local", ".internal")) or "%" in host:
            raise ValueError()
        if any(_SENSITIVE_QUERY.search(key) or re.sub(r"[^a-z]", "", key.lower()).endswith(
                ("apikey", "token", "secret", "password", "signature", "sessionid"))
               for key, _ in parse_qsl(parts.query, keep_blank_values=True)):
            raise ValueError()
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            literal = None
        if literal is not None and not _public_ip(str(literal)):
            raise ValueError()
    except (ValueError, UnicodeError):
        raise FetchError("invalid_url", "Gunakan URL publik tanpa kredensial, token, atau port khusus.") from None
    netloc = f"[{host}]" if ":" in host else host
    if port:
        netloc += f":{port}"
    return urlunsplit((parts.scheme, netloc, quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~"),
                       quote(parts.query, safe="%=&/?@:!$'()*+,;-._~"), ""))


def _resolve_public(url):
    parts = urlsplit(url)
    try:
        answers = socket.getaddrinfo(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80), type=socket.SOCK_STREAM)
        addresses = list(dict.fromkeys(answer[4][0] for answer in answers))
        if not addresses or not all(_public_ip(address) for address in addresses):
            raise FetchError("blocked_address", "Alamat sumber bukan tujuan internet publik.")
        return addresses
    except (socket.gaierror, OSError):
        raise FetchError("network_error", "Nama domain sumber tidak dapat dihubungi.") from None


def _request_once(url, addresses, *, accept, max_bytes):
    """Pin the checked DNS result; TLS still validates the original hostname."""
    parts = urlsplit(url)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    deadline = time.monotonic() + REQUEST_TIMEOUT
    connection = None
    sock = None
    try:
        sock = socket.create_connection((addresses[0], port), timeout=REQUEST_TIMEOUT)
        if parts.scheme == "https":
            context = ssl.create_default_context()
            sock = context.wrap_socket(sock, server_hostname=parts.hostname)
            connection = http.client.HTTPSConnection(parts.hostname, port, timeout=REQUEST_TIMEOUT, context=context)
        else:
            connection = http.client.HTTPConnection(parts.hostname, port, timeout=REQUEST_TIMEOUT)
        connection.sock = sock
        path = urlunsplit(("", "", parts.path or "/", parts.query, ""))
        connection.request("GET", path, headers={"User-Agent": USER_AGENT, "Accept": accept,
                                                "Accept-Encoding": "identity", "Connection": "close"})
        response = connection.getresponse()
        headers = {key.lower(): value for key, value in response.getheaders()}
        if headers.get("content-encoding", "identity").lower() not in ("", "identity"):
            raise FetchError("unsupported_content", "Sumber mengirim kompresi yang tidak didukung.")
        length = headers.get("content-length", "")
        if length.isdigit() and int(length) > max_bytes:
            raise FetchError("too_large", "Respons sumber melebihi batas ukuran.")
        body = bytearray()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise FetchError("network_error", "Waktu pemeriksaan sumber habis.")
            sock.settimeout(remaining)
            chunk = response.read1(min(65536, max_bytes + 1 - len(body)))
            if not chunk:
                break
            body.extend(chunk)
            if len(body) > max_bytes:
                raise FetchError("too_large", "Respons sumber melebihi batas ukuran.")
            if response.isclosed():
                break
        return {"url": url, "status": response.status, "headers": headers, "body": bytes(body)}
    except (OSError, http.client.HTTPException, TimeoutError):
        raise FetchError("network_error", "Sumber tidak dapat dihubungi atau waktunya habis.") from None
    finally:
        if connection is not None:
            connection.close()
        elif sock is not None:
            sock.close()


def _robots_permits(url):
    parts = urlsplit(url)
    robots_url = urlunsplit((parts.scheme, parts.netloc, "/robots.txt", "", ""))
    try:
        result = _fetch(robots_url, accept="text/plain", max_bytes=256 * 1024)
    except FetchError:
        raise FetchError("robots_unknown", "robots.txt belum dapat diperiksa; buka sumber secara manual.") from None
    if result["status"] == 404:
        return
    text = result["body"].decode("utf-8", errors="replace")
    if result["status"] != 200 or not is_robots_body(text) or has_undecodable_rule(text):
        raise FetchError("robots_unknown", "Kebijakan robots.txt belum dapat dipastikan; buka sumber secara manual.")
    path = unquote(parts.path or "/") + ("?" + unquote(parts.query) if parts.query else "")
    if not all(allowed(text, agent, path) for agent in ("SuperWork", "*")):
        raise FetchError("robots_blocked", "Sumber membatasi pembacaan otomatis melalui robots.txt; buka sumber secara manual.")


def _fetch(url, *, accept="application/json", max_bytes=MAX_RESPONSE_BYTES, check_robots=False):
    current = _validate_url(url)
    for _ in range(4):
        addresses = _resolve_public(current)
        if check_robots:
            _robots_permits(current)
        result = _request_once(current, addresses, accept=accept, max_bytes=max_bytes)
        if result["status"] not in (301, 302, 303, 307, 308):
            return result
        location = result["headers"].get("location")
        if not location:
            raise FetchError("redirect_error", "Sumber mengirim pengalihan tanpa tujuan.")
        current = _validate_url(urljoin(current, location))
    raise FetchError("redirect_error", "Terlalu banyak pengalihan pada sumber.")


def _fetch_json(url):
    result = _fetch(url)
    if result["status"] != 200:
        raise FetchError("source_error", f"API sumber menjawab HTTP {result['status']}; coba lagi nanti.")
    if "json" not in result["headers"].get("content-type", "").lower():
        raise FetchError("source_error", "API sumber tidak mengirim JSON.")
    try:
        return json.loads(result["body"])
    except (ValueError, UnicodeError, RecursionError):
        raise FetchError("source_error", "JSON dari sumber tidak valid.") from None


def _text(value, limit=20000):
    if not isinstance(value, (str, int, float)):
        return ""
    text = html.unescape(html.unescape(str(value)[:limit * 2]))
    return re.sub(r"\s+", " ", re.sub(r"<[^>]*>", " ", text)).strip()[:limit]


def _norm(value):
    value = "".join(c for c in unicodedata.normalize("NFKD", str(value)) if not unicodedata.combining(c)).casefold()
    value = re.sub(r"front[\s-]*end", "frontend", value)
    value = re.sub(r"\bdeveloper\b", "engineer", value)
    value = re.sub(r"\b(?:administrasi|administration)\b", "admin", value)
    value = _INTERNSHIP.sub("magang", value)
    value = re.sub(r"(?<!\w)c\+\+(?!\w)", "cplusplus", value)
    value = re.sub(r"(?<!\w)c#(?!\w)", "csharp", value)
    return re.sub(r"[^\w]+", " ", value).strip()


def _employment(values, title):
    """Job types can mix seniority and contracts; only recognised types count."""
    values = values if isinstance(values, list) else [values]
    types = [_norm(_text(value, 150)).replace("_", " ") for value in values[:20]]
    program_staff = re.search(r"\b(?:internships?|interns?|magang)\s+program(?:me)?\b", title, re.I) and re.search(
        r"\b(?:manager|coordinator|director|head|lead)\b", title, re.I)
    if any("magang" in value.split() for value in types) or (_INTERNSHIP.search(title) and not program_staff):
        return "Magang"
    names = {"full time": "Penuh waktu", "fulltime": "Penuh waktu", "penuh waktu": "Penuh waktu",
             "part time": "Paruh waktu", "parttime": "Paruh waktu", "paruh waktu": "Paruh waktu",
             "contract": "Kontrak", "contractor": "Kontrak", "contractual": "Kontrak", "kontrak": "Kontrak"}
    return next((names[value] for value in types if value in names), UNKNOWN)


def _level(value):
    names = {"pemula": "Pemula", "entry": "Pemula", "entry level": "Pemula",
             "junior": "Junior", "menengah": "Menengah", "middle": "Menengah",
             "mid": "Menengah", "mid level": "Menengah", "senior": "Senior"}
    return names.get(_norm(_text(value, 100)).replace("_", " "), UNKNOWN)


def _country_code(value):
    target = str(value).strip().casefold()
    for code, names in _COUNTRIES.items():
        if target in [code.casefold(), *(name.casefold() for name in names)]:
            return code
    return str(value).strip().upper() if len(str(value).strip()) == 2 else None


def _country(value):
    code = _country_code(value)
    return _COUNTRIES[code][0] if code in _COUNTRIES else _text(value, 100) or UNKNOWN


def _date(value):
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return datetime.fromtimestamp(value, timezone.utc)
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def _canonical_url(url):
    parts = urlsplit(url)
    query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
             if not key.casefold().startswith("utm_") and key.casefold() not in ("ref", "source", "fbclid", "gclid")]
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/") or "/", urlencode(sorted(query)), ""))


def _base_job(source, checked, *, title, company, url, description="", location="", countries=None,
              cities=None, skills=None, mode="", employment="", level="", sector="", published=None, source_id=""):
    try:
        url = _validate_url(url)
    except FetchError:
        return None
    title = _text(title, 300)
    if not title:
        return None
    countries = [_country(c) for c in (countries or [])[:20] if isinstance(c, str) and c.strip()]
    if not countries:
        for code, names in _COUNTRIES.items():
            if any(re.search(r"(?:^|[,;\s])" + re.escape(name) + r"(?:$|[,;\s])", location, re.I) for name in names if len(name) > 2):
                countries = [_COUNTRIES[code][0]]
                break
    date = _date(published)
    mode = {"remote": "Remote", "hybrid": "Hybrid", "onsite": "On-site", "on-site": "On-site"}.get(str(mode).lower(), UNKNOWN)
    employment = _employment(employment, title)
    skills = [_text(s, 100) for s in (skills or [])[:40] if isinstance(s, str)]
    evidence = {"url": url, "checked_at": checked, "http_status": None, "status": "unknown", "origin": "unknown",
                "source_api": source["api"], "message": "Tercantum di respons API. Halaman penerbit, tenggat, dan identitas perusahaan belum diperiksa."}
    return {"id": int.from_bytes(hashlib.sha256(_canonical_url(url).encode()).digest()[:6], "big") + 1000000,
            "title": title, "company": _text(company, 200) or UNKNOWN, "country": countries[0] if countries else UNKNOWN,
            "countries": countries, "region": UNKNOWN, "city": _text((cities or [location])[0], 200) or UNKNOWN,
            "location": _text(location, 300), "mode": mode, "employment": employment,
            "level": _level(level), "sector": _text(sector, 100) or UNKNOWN, "skills": skills,
            "description": _text(description), "sourceType": source.get("sourceType", "portal"), "sourceName": source["name"],
            "sourceUrl": url, "foundOn": source["name"], "primarySourceName": source["name"], "sourceId": _text(source_id, 200),
            "origin": "unknown", "status": "unknown", "checkedAt": checked, "publishedAt": date.isoformat() if date else None,
            "postedDays": max(0, (datetime.now(timezone.utc) - date).days) if date else None,
            "deadline": None, "eligible": [], "mock": False, "evidence": evidence, "evidenceText": evidence["message"],
            "salary": "Gaji belum dicantumkan"}


def _matches(job, query, filters):
    haystack = _norm(" ".join([job["title"], job["company"], job["sector"], job["employment"], job["description"], *job["skills"]]))
    words = set(haystack.split())
    native_match = isinstance(job.get('searchQuery'), str) and _norm(job['searchQuery']) == _norm(query)
    if not all(term in words if term in ("c", "cplusplus", "csharp") else native_match or term in haystack for term in _norm(query).split()):
        return False
    if filters.get("countries") and not set(map(_country, filters["countries"])).intersection(job["countries"]):
        return False
    for field in ("region", "city", "mode", "employment", "sector", "sourceType", "sourceName"):
        if filters.get(field) and _norm(filters[field]) != _norm(job[field]):
            return False
    if filters.get("level"):
        level = _level(filters["level"])
        if level == UNKNOWN and _norm(filters["level"]) != _norm(UNKNOWN):
            return False
        if level != _level(job["level"]):
            return False
    if filters.get("eligible") and filters["eligible"] not in job["eligible"]:
        return False
    if filters.get("recency") and (job["postedDays"] is None or job["postedDays"] > int(filters["recency"])):
        return False
    status = filters.get("status", "active")
    if status in ("", "active"):
        return job["status"] not in ("closed", "expired")
    return status == "all" or status == job["status"]


def portal_search_url(provider_id, query, filters=None):
    if (provider_id not in ('glints', 'jobstreet') or not isinstance(query, str)
            or not query.strip() or len(query) > 200 or not any(char.isalnum() for char in query)
            or filters is not None and not isinstance(filters, dict)):
        raise ValueError('Portal atau kata kunci tidak valid; gunakan Glints/Jobstreet dan maksimal 200 karakter.')
    query = query.strip().lower()
    if _norm((filters or {}).get('employment', '')) == 'magang' and not _INTERNSHIP.search(query):
        query = 'magang ' + query
    slug = quote(re.sub(r'\s+', '-', query), safe='')
    return ('https://glints.com/id/explore/' + slug if provider_id == 'glints'
            else 'https://id.jobstreet.com/' + slug + '-jobs')


class _ListingPage(HTMLParser):
    """Read the public listing embedded in the site's rendered search page."""
    def __init__(self):
        super().__init__()
        self.capturing = False
        self.chunks = []
        self.data = None
        self.links = []
        self.next_links = []
        self.anchor = None
        self.anchor_text = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == 'a':
            if len(self.links) >= MAX_PAGE_ROWS:
                raise FetchError('too_large', 'Halaman sumber melebihi batas pemrosesan per permintaan.')
            self.links.append(attributes.get('href'))
            self.anchor, self.anchor_text = attributes, []
        if (tag in ('a', 'link') and attributes.get('aria-disabled') != 'true'
                and 'next' in attributes.get('rel', '').lower().split()):
            self.next_links.append(attributes.get('href'))
        if tag == 'script' and attributes.get('id') == '__NEXT_DATA__':
            self.capturing = True
            self.chunks = []

    def handle_data(self, value):
        if self.capturing:
            self.chunks.append(value)
        if self.anchor is not None:
            self.anchor_text.append(value)

    def handle_endtag(self, tag):
        if tag == 'a' and self.anchor is not None:
            label = self.anchor.get('aria-label') or ''.join(self.anchor_text)
            if (self.anchor.get('aria-disabled') != 'true'
                    and re.fullmatch(r'\s*(?:next(?: page)?|berikutnya|selanjutnya)\s*[>»→]?\s*', label, re.I)):
                self.next_links.append(self.anchor.get('href'))
            self.anchor, self.anchor_text = None, []
        if tag == 'script' and self.capturing:
            try:
                self.data = json.loads(''.join(self.chunks))
            except (ValueError, RecursionError):
                self.data = None
            self.capturing = False


def _kalibrr_jobs(source, query, filters, checked):
    result = _fetch(source['api'], accept='text/html', max_bytes=2 * 1024 * 1024, check_robots=True)
    if result['status'] != 200 or 'html' not in result['headers'].get('content-type', '').lower():
        raise FetchError('source_error', f"Kalibrr menjawab HTTP {result['status']} atau format berubah; buka portal resmi untuk memeriksa.")
    parser = _ListingPage()
    parser.feed(decode_page(result['body']))
    data = parser.data
    props = data.get('props', {}).get('pageProps', {}) if isinstance(data, dict) and isinstance(data.get('props'), dict) else {}
    rows = props.get('jobs') if isinstance(props, dict) else None
    if not isinstance(rows, list):
        raise FetchError('source_error', 'Format halaman Kalibrr berubah atau meminta login; hasil belum dapat dibaca. Buka portal resmi.')
    if len(rows) > MAX_PAGE_ROWS:
        raise FetchError('too_large', 'Halaman sumber melebihi batas pemrosesan per permintaan.')
    hits = []
    for row in rows:
        if not isinstance(row, dict) or row.get('visibility') != 'public' or type(row.get('id')) is not int or not 0 < row['id'] <= 2**53 - 1:
            continue
        company = row.get('company') if isinstance(row.get('company'), dict) else {}
        code, slug = company.get('code', ''), row.get('slug', '')
        if not all(isinstance(v, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,200}', v) for v in (code, slug)):
            continue
        location = row.get('googleLocation') if isinstance(row.get('googleLocation'), dict) else {}
        address = location.get('addressComponents') if isinstance(location.get('addressComponents'), dict) else {}
        skills = row.get('jobSdsSkills') if isinstance(row.get('jobSdsSkills'), list) else []
        skills = [item['sdsSkill'].get('name') for item in skills[:40] if isinstance(item, dict) and isinstance(item.get('sdsSkill'), dict)]
        experience = row.get('workExperience') if type(row.get('workExperience')) is int else None
        job = _base_job(source, checked, title=row.get('name'), company=company.get('name') or row.get('companyName'),
                        url=f"https://www.kalibrr.id/c/{code}/jobs/{row['id']}/{slug}",
                        description=_text(row.get('description'))+' '+_text(row.get('qualifications')),
                        location=', '.join(_text(address.get(k), 200) for k in ('city', 'region', 'country') if address.get(k)),
                        countries=[address['country']] if isinstance(address.get('country'), str) else [],
                        cities=[_text(address.get('city'))], skills=skills,
                        employment=[row.get('tenure'), 'Internship' if experience == 100 else ''],
                        level={100: 'entry', 200: 'junior', 300: 'middle', 400: 'senior'}.get(experience, ''),
                        mode='remote' if row.get('isWorkFromHome') is True else 'hybrid' if row.get('isHybrid') is True else '',
                        sector=row.get('function'), published=row.get('activationDate'), source_id=row['id'])
        if not job:
            continue
        job['region'] = _text(address.get('region'), 200) or UNKNOWN
        expires = _date(row.get('applicationEndDate'))
        if expires and re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(row['applicationEndDate'])):
            expires = datetime.combine(expires.date(), day_time.max, timezone.utc)
        if expires:
            job['deadline'] = expires.isoformat()
            if expires < _date(checked):
                job['status'] = 'expired'
        job['evidence'].update(method='public_page', source_page=source['api'], http_status=200,
                               status=job['status'], valid_through=job['deadline'],
                               message='Tercantum di halaman pencarian publik Kalibrr. Tenggat mengikuti data sumber bila tersedia. Identitas perusahaan dan penerimaan lamaran belum terkonfirmasi.')
        job['evidenceText'] = job['evidence']['message']
        if _matches(job, query, filters):
            hits.append(job)
    next_cursor = next((validate_source_cursor(source, link) for link in parser.next_links if link), None)
    return {'jobs': hits, 'next_cursor': next_cursor, 'fetched_count': len(rows)}


def _portal_job_url(provider_id, value):
    if not isinstance(value, str):
        return None
    origin = 'https://glints.com' if provider_id == 'glints' else 'https://id.jobstreet.com'
    try:
        parts = urlsplit(_validate_url(urljoin(origin, value)))
    except ValueError:
        return None
    hosts = ('glints.com', 'www.glints.com') if provider_id == 'glints' else ('id.jobstreet.com',)
    pattern = (r'/id/opportunities/jobs/[^/]+/([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})'
               if provider_id == 'glints' else r'/(?:id/)?job/([0-9]{1,20})')
    match = re.fullmatch(pattern, parts.path.rstrip('/'))
    if (parts.scheme != 'https' or parts.hostname not in hosts or parts.port not in (None, 443) or not match
            or any(unquote(part) in ('.', '..') or '/' in unquote(part) or '\\' in unquote(part) for part in parts.path.split('/'))):
        return None
    return urlunsplit(('https', urlsplit(origin).netloc, parts.path.rstrip('/'), '', '')), match[1]


def _portal_availability(job, status, deadline, checked):
    job['status'] = {'OPEN': 'open', 'CLOSED': 'closed', 'EXPIRED': 'expired'}.get(_text(status, 50).upper(), 'unknown')
    expires = _date(deadline)
    if expires and re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(deadline)):
        expires = datetime.combine(expires.date(), day_time.max, timezone.utc)
    if expires:
        job['deadline'] = expires.isoformat()
        if expires < _date(checked) and job['status'] != 'closed':
            job['status'] = 'expired'
    job['evidence'].update(status=job['status'], valid_through=job['deadline'])


def parse_glints_page(page, query, filters, checked, *, native_search=False):
    """Read public listing data only; cache entries without listing links are ignored."""
    if not isinstance(page, str) or len(page) > MAX_RESPONSE_BYTES:
        raise FetchError('source_error', 'Halaman Glints tidak valid atau melebihi batas ukuran.')
    try:
        if len(page.encode('utf-8')) > MAX_RESPONSE_BYTES:
            raise ValueError
    except (ValueError, UnicodeError):
        raise FetchError('source_error', 'Halaman Glints tidak valid atau melebihi batas ukuran.') from None
    parser = _ListingPage()
    parser.feed(page)
    data = parser.data
    props = data.get('props') if isinstance(data, dict) else None
    cache = props.get('apolloCache') if isinstance(props, dict) else None
    if not isinstance(cache, dict):
        raise FetchError('source_error', 'Format pencarian Glints berubah atau meminta tindakan Anda; hasil belum dapat dibaca.')
    def resolve(value):
        if not isinstance(value, dict):
            return {}
        target = cache.get(value.get('__ref')) if isinstance(value.get('__ref'), str) else value
        return target if isinstance(target, dict) else {}
    root = cache.get('ROOT_QUERY', {})
    empty = isinstance(root, dict) and any(
        re.match(r'^(?:searchJobs|exploreJobs|jobs)(?:V\d+)?(?:\(|$)', name, re.I)
        and (value == [] or any(resolve(value).get(key) == [] for key in ('jobs', 'edges', 'nodes', 'data')))
        for name, value in root.items() if isinstance(name, str))
    linked = []
    for link in parser.links:
        parsed = _portal_job_url('glints', link)
        if parsed:
            row = cache.get('Job:' + parsed[1])
            if isinstance(row, dict) and row.get('id') == parsed[1] and isinstance(row.get('title'), str):
                linked.append((parsed, row))
    if not linked and not empty:
        raise FetchError('source_error', 'Glints belum menampilkan kontrak hasil pencarian yang dapat dibaca; periksa halaman di browser.')
    source = {'name': 'Glints', 'api': portal_search_url('glints', query, filters)}
    hits, seen = [], set()
    for (url, source_id), row in linked:
        company, city, country = (resolve(row.get(key)) for key in ('company', 'city', 'country'))
        country_name = _text(country.get('name') or country.get('code'), 100)
        city_name = _text(city.get('name'), 200)
        skills = row.get('skills') if isinstance(row.get('skills'), list) else []
        job = _base_job(source, checked, title=row['title'], company=company.get('name'), url=url,
                        description=row.get('description'),
                        location=', '.join(value for value in (city_name, country_name) if value),
                        countries=[country_name] if country_name else [], cities=[city_name],
                        skills=[resolve(skill).get('name') for skill in skills[:40]], employment=row.get('type'),
                        mode=row.get('workArrangementOption'), published=row.get('createdAt'), source_id=source_id)
        if not job or url in seen:
            continue
        job['providerId'] = 'glints'
        if native_search:
            job['searchQuery'] = query
        _portal_availability(job, row.get('status'), row.get('validThrough') or row.get('expiryDate'), checked)
        job['evidence'].update(method='public_page', source_page=source['api'], status=job['status'],
                               message='Tercantum di halaman pencarian Glints. Status mengikuti label sumber bila tersedia; tenggat, identitas perusahaan dan penerimaan lamaran belum terkonfirmasi.')
        job['evidenceText'] = job['evidence']['message']
        if _matches(job, query, filters):
            hits.append(job)
            seen.add(url)
    return hits


def parse_jobstreet_cards(cards, query, filters, checked, *, native_search=False):
    """Parse public browser cards; the caller must establish an explicit empty state."""
    if not isinstance(cards, list) or cards and not any(
            isinstance(row, dict) and isinstance(row.get('title'), str) and isinstance(row.get('url'), str) for row in cards[:MAX_PAGE_ROWS]):
        raise FetchError('source_error', 'Kartu pencarian Jobstreet belum dapat dibaca; periksa halaman di browser.')
    if len(cards) > MAX_PAGE_ROWS:
        raise FetchError('too_large', 'Halaman sumber melebihi batas pemrosesan per permintaan.')
    source = {'name': 'Jobstreet', 'api': portal_search_url('jobstreet', query, filters)}
    hits, seen = [], set()
    for row in cards:
        if (not isinstance(row, dict) or any(not isinstance(row.get(key, ''), str)
                                            for key in ('id', 'title', 'url', 'company', 'location', 'description', 'employment', 'salary'))
                or row.get('posted') is not None and not isinstance(row['posted'], str)):
            continue
        parsed = _portal_job_url('jobstreet', row.get('url'))
        if not parsed or parsed[0] in seen:
            continue
        url, source_id = parsed
        location = _text(row.get('location'), 300)
        parts = [value.strip() for value in location.split(',')]
        job = _base_job(source, checked, title=row.get('title'), company=row.get('company'), url=url,
                        description=row.get('description'), location=location, cities=parts[:1],
                        employment=row.get('employment'), published=row.get('posted'), source_id=source_id)
        if not job:
            continue
        if not job['countries']:
            job.update(countries=['Indonesia'], country='Indonesia')
        job.update(providerId='jobstreet', region=parts[1] if len(parts) > 1 else UNKNOWN,
                   salary=_text(row.get('salary'), 200) or job['salary'])
        if native_search:
            job['searchQuery'] = query
        _portal_availability(job, row.get('status'), row.get('deadline'), checked)
        job['evidence'].update(method='local_browser', source_page=source['api'],
                               message='Tercantum di kartu pencarian Jobstreet Indonesia. Negara mengikuti lokasi bila eksplisit, selain itu pasar portal Indonesia. Halaman detail, tenggat dan identitas perusahaan belum diperiksa.')
        job['evidenceText'] = job['evidence']['message']
        if _matches(job, query, filters):
            hits.append(job)
            seen.add(url)
    return hits


def validate_source_cursor(source, value):
    """A public API continuation may advance paging only, never change its target or filters."""
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise FetchError('invalid_cursor', 'Penanda halaman sumber tidak valid.')
    base = urlsplit(source['api'])
    try:
        target_url = _validate_url(urljoin(source['api'], value))
        target = urlsplit(target_url)
        if (target.scheme != 'https' or target.hostname != base.hostname or target.port != base.port
                or target.path != base.path or target.fragment):
            raise ValueError
        paging_key = {'freehire': 'offset', 'arbeitnow': 'page', 'kalibrr': 'page', 'lever': 'skip'}.get(source['kind'])
        if paging_key is None:
            raise ValueError
        before = parse_qsl(base.query, keep_blank_values=True)
        after = parse_qsl(target.query, keep_blank_values=True)
        if sorted((k, v) for k, v in before if k != paging_key) != sorted((k, v) for k, v in after if k != paging_key):
            raise ValueError
        values = [v for k, v in after if k == paging_key]
        current = dict(before).get(paging_key, '1' if paging_key == 'page' else '0')
        if len(values) != 1 or not re.fullmatch(r'[0-9]{1,10}', values[0]) or int(values[0]) <= int(current):
            raise ValueError
        if source['kind'] == 'freehire' and int(values[0]) + int(dict(before).get('limit', RESULT_LIMIT)) > 10000:
            raise ValueError
    except (ValueError, TypeError):
        raise FetchError('invalid_cursor', 'Penanda halaman harus melanjutkan sumber dan pencarian yang sama.') from None
    return target_url


def _source_page(source, payload, rows, hits):
    result = {'jobs': hits, 'next_cursor': None, 'fetched_count': len(rows)}
    kind = source['kind']
    if kind == 'arbeitnow':
        links = payload.get('links')
        if isinstance(links, dict) and links.get('next'):
            result['next_cursor'] = validate_source_cursor(source, links['next'])
    elif kind == 'freehire':
        meta = payload.get('meta')
        if meta is not None:
            if not isinstance(meta, dict) or not all(type(meta.get(key)) is int for key in ('total', 'limit', 'offset')):
                raise FetchError('source_error', 'Metadata halaman Freehire tidak sesuai kontrak.')
            total, limit, offset = (meta[key] for key in ('total', 'limit', 'offset'))
            current = dict(parse_qsl(urlsplit(source['api']).query))
            if total < 0 or limit <= 0 or offset != int(current.get('offset', 0)) or limit != int(current['limit']):
                raise FetchError('source_error', 'Metadata halaman Freehire tidak sesuai permintaan.')
            if rows and offset + limit < total:
                if offset + 2 * limit > 10000:
                    result['pagination_limited'] = True
                else:
                    parts = urlsplit(source['api'])
                    params = [(key, str(offset + limit) if key == 'offset' else value)
                              for key, value in parse_qsl(parts.query, keep_blank_values=True)]
                    result['next_cursor'] = validate_source_cursor(source, urlunsplit(parts._replace(query=urlencode(params))))
    elif kind == 'lever':
        parts = urlsplit(source['api'])
        params = dict(parse_qsl(parts.query))
        limit, skip = int(params.get('limit', 500)), int(params.get('skip', 0))
        if len(rows) == limit:
            params['skip'] = str(skip + limit)
            result['next_cursor'] = validate_source_cursor(source, urlunsplit(parts._replace(query=urlencode(params))))
    return result


def _source_jobs(source, query, filters, checked):
    if source['kind'] == 'kalibrr':
        return _kalibrr_jobs(source, query, filters, checked)
    payload = _fetch_json(source["api"])
    kind = source["kind"]
    rows = payload if kind == "lever" else payload.get("jobs" if kind == "greenhouse" else "data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise FetchError("source_error", "Bentuk respons sumber tidak sesuai kontrak.")
    if len(rows) > MAX_PAGE_ROWS:
        raise FetchError('too_large', 'Halaman sumber melebihi batas pemrosesan per permintaan.')
    hits = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if kind == "freehire":
            enrich = row.get("enrichment") if isinstance(row.get("enrichment"), dict) else {}
            job = _base_job(source, checked, title=row.get("title"), company=row.get("company"), url=row.get("url"),
                            description=row.get("description"), location=_text(row.get("location")), countries=row.get("countries") if isinstance(row.get("countries"), list) else [],
                            cities=row.get("cities") if isinstance(row.get("cities"), list) else [], skills=row.get("skills") if isinstance(row.get("skills"), list) else [],
                            mode=row.get("work_mode"), employment=[row.get("employment_type"), enrich.get("employment_type")], level=enrich.get("seniority"), sector=enrich.get("category"),
                            published=row.get("posted_at"), source_id=row.get("public_slug"))
        elif kind == "arbeitnow":
            job = _base_job(source, checked, title=row.get("title"), company=row.get("company_name"), url=row.get("url"),
                            description=row.get("description"), location=_text(row.get("location")), countries=[row["country"]] if isinstance(row.get("country"), str) else [],
                            skills=row.get("tags") if isinstance(row.get("tags"), list) else [], mode="remote" if row.get("remote") is True else "",
                            employment=row.get("job_types") if isinstance(row.get("job_types"), list) else [],
                            published=row.get("created_at"), source_id=row.get("slug"))
        elif kind == "greenhouse":
            location = row.get("location") if isinstance(row.get("location"), dict) else {}
            departments = row.get("departments") if isinstance(row.get("departments"), list) else []
            job = _base_job(source, checked, title=row.get("title"), company=source["slug"], url=row.get("absolute_url"),
                            description=row.get("content"), location=_text(location.get("name")),
                            sector=departments[0].get("name") if departments and isinstance(departments[0], dict) else "", source_id=row.get("id"))
        else:
            categories = row.get("categories") if isinstance(row.get("categories"), dict) else {}
            job = _base_job(source, checked, title=row.get("text"), company=source["slug"], url=row.get("hostedUrl"),
                            description=row.get("descriptionPlain") or row.get("description"), location=_text(categories.get("location")),
                            countries=[row["country"]] if row.get("country") else [], mode=row.get("workplaceType"),
                            employment=categories.get("commitment"), level=categories.get("level"), sector=categories.get("team"), source_id=row.get("id"))
        if job and _matches(job, query, filters):
            hits.append(job)
    return _source_page(source, payload, rows, hits)


def _search_inputs(query, filters, sources, boards):
    if not isinstance(query, str) or not query.strip() or len(query) > 200:
        raise ValueError("Isi kata kunci pencarian, maksimal 200 karakter.")
    filters = {} if filters is None else filters
    sources = list(DEFAULT_SOURCES) if sources is None else sources
    boards = [] if boards is None else boards
    if not isinstance(filters, dict) or not isinstance(sources, list) or len(sources) > len(DEFAULT_SOURCES) or not isinstance(boards, list) or len(boards) > 6:
        raise ValueError("Filter atau daftar sumber tidak valid; maksimal enam board perusahaan.")
    if not all(isinstance(s, str) and s in DEFAULT_SOURCES for s in sources):
        raise ValueError("Sumber pencarian tidak dikenal.")
    if any(not isinstance(key, str) for key in filters):
        raise ValueError("Nama filter tidak valid.")
    countries = filters.get("countries", [])
    if not isinstance(countries, list) or len(countries) > 20 or any(not isinstance(c, str) or len(c) > 100 for c in countries):
        raise ValueError("Filter negara harus berupa daftar nama negara.")
    for key, value in filters.items():
        if key == "countries":
            continue
        if key not in ("region", "city", "mode", "employment", "level", "sector", "sourceType", "sourceName", "eligible", "recency", "status") or not isinstance(value, str) or len(value) > 150:
            raise ValueError("Filter pencarian tidak valid.")
    if filters.get("recency") and (not filters["recency"].isdigit() or not 1 <= int(filters["recency"]) <= 3650):
        raise ValueError("Batas umur lowongan harus 1 sampai 3650 hari.")
    definitions = []
    for source in SOURCE_DEFINITIONS:
        if source["id"] not in sources:
            continue
        definition = dict(source, kind=source['id'] if source['id'] in ('freehire', 'kalibrr') else 'manual' if source.get('mode') in ('portal', 'web') else 'arbeitnow')
        if definition["kind"] == "freehire":
            provider_query = _INTERNSHIP.sub("internship", query)
            params = [("limit", str(RESULT_LIMIT)), ("offset", "0"), ("description_format", "text")]
            if _norm(filters.get("employment", "")) == "magang":
                # The public facet does the internship search; retain other role keywords.
                params.append(("employment_type", "internship"))
                provider_query = re.sub(r"\s+", " ", _INTERNSHIP.sub(" ", query)).strip()
            if provider_query:
                params.append(("q", provider_query))
            params += [("countries", code) for code in map(_country_code, countries) if code]
            if filters.get("recency"):
                params.append(("posted_within_days", filters["recency"]))
            if filters.get("mode") in ("Remote", "Hybrid", "On-site"):
                params.append(("work_mode", {"Remote": "remote", "Hybrid": "hybrid", "On-site": "onsite"}[filters["mode"]]))
            definition["api"] = "https://freehire.me/api/v1/agent/jobs/search?" + urlencode(params)
        elif definition['kind'] == 'kalibrr':
            definition['api'] = 'https://www.kalibrr.id/id-ID/home/te/' + quote(query.strip(), safe='')
            definition['search_url'] = definition['api']
        elif definition['kind'] == 'manual':
            portal_query = query.strip()
            if filters.get('employment') == 'Magang' and not _INTERNSHIP.search(portal_query):
                portal_query = 'magang ' + portal_query
            definition['portal_query'] = portal_query
            if source.get('mode') == 'web':
                discovery = ('(inurl:careers OR inurl:career OR inurl:karir OR site:jobs.lever.co OR site:boards.greenhouse.io)'
                             if source['id'] == 'google-careers' else '(lowongan OR careers OR jobs)')
                definition['api'] = definition['search_url'] = 'https://www.google.com/search?' + urlencode({'q': portal_query + ' ' + discovery})
            else:
                definition['search_url'] = portal_search_url(source['id'], query, filters)
                definition['skip'] = (bool(countries) and not any(_country_code(c) == 'ID' for c in countries)
                                      or filters.get('sourceType') not in (None, '', 'portal')
                                      or bool(filters.get('sourceName')) and _norm(filters['sourceName']) != _norm(source['name']))
        else:
            domain = "www.arbeitnow.co.uk" if source["id"] == "arbeitnow-uk" else "www.arbeitnow.com"
            definition["api"] = f"https://{domain}/api/job-board-api?page=1"
        definitions.append(definition)
    seen = set()
    for board in boards:
        if not isinstance(board, dict) or board.get("type") not in ("greenhouse", "lever") or not isinstance(board.get("slug"), str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", board["slug"]):
            raise ValueError("Board perusahaan harus Greenhouse atau Lever dengan slug yang valid.")
        kind, slug = board["type"], board["slug"]
        if (kind, slug.lower()) in seen:
            continue
        seen.add((kind, slug.lower()))
        api = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true" if kind == "greenhouse" else f"https://api.lever.co/v0/postings/{slug}?mode=json&limit=500"
        definitions.append({"id": f"{kind}:{slug}", "name": f"{kind.title()} · {slug}", "kind": kind, "slug": slug,
                            "api": api, "sourceType": "company", "scope": "Lowongan board perusahaan pilihan pengguna; seluruh kecocokan dalam respons ditampilkan, dilanjutkan bila sumber mendukung halaman berikutnya."})
    return query.strip(), filters, definitions


def search_jobs(query, filters=None, sources=None, boards=None, *, portal_search=None, cursors=None):
    """Search selected public corpora. No geography is forced or inferred from CV."""
    checked = _stamp()
    try:
        if portal_search is not None and not callable(portal_search):
            raise ValueError('Callback pencarian portal tidak valid.')
        query, filters, definitions = _search_inputs(query, filters, sources, boards)
        if cursors is not None:
            if (not isinstance(cursors, dict) or any(key not in {d['id'] for d in definitions} for key in cursors)
                    or any(not isinstance(value, str) or not value or len(value) > 2048 for value in cursors.values())):
                raise ValueError('Penanda halaman harus berasal dari sumber pencarian yang dipilih.')
            definitions = [definition for definition in definitions if definition['id'] in cursors]
            for definition in definitions:
                cursor = cursors[definition['id']]
                definition['cursor'] = cursor
                if definition['kind'] != 'manual':
                    definition['api'] = validate_source_cursor(definition, cursor)
                    if definition.get('search_url'):
                        definition['search_url'] = definition['api']
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    jobs, statuses, seen = [], [], set()
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = []
        for definition in definitions:
            if definition['kind'] == 'manual':
                # The browser runtime is single-threaded; one batch must not fill its own queue.
                future = None
            else:
                future = executor.submit(_source_jobs, definition, query, filters, checked)
            futures.append(future)
        for definition, future in zip(definitions, futures):
            status = {"id": definition["id"], "name": definition["name"], "scope": definition["scope"], "checked_at": checked,
                      "next_cursor": None, "fetched_count": 0}
            if definition.get('search_url'):
                status['search_url'] = definition['search_url']
            if future is None and (not portal_search or definition.get('skip')):
                skipped = definition.get('skip')
                status.update(status='skipped' if skipped else 'manual', count=None, checked_at=None, query=definition['portal_query'],
                              message='Portal ini tidak sesuai filter negara atau sumber yang dipilih.' if skipped else 'Cari di portal resmi, lalu catat tautan lowongan di dashboard. Hasil belum diambil otomatis; sesuaikan filter lain di portal.')
                if skipped:
                    status.pop('search_url', None)
                statuses.append(status)
                continue
            try:
                page = portal_search(definition, query, filters, checked) if future is None else future.result()
                hits = page.get('jobs') if isinstance(page, dict) else page
                if not isinstance(hits, list) or len(hits) > MAX_PAGE_ROWS:
                    raise FetchError('source_error', 'Bentuk hasil pencarian sumber tidak sesuai kontrak.')
                next_cursor = page.get('next_cursor') if isinstance(page, dict) else None
                fetched_count = page.get('fetched_count', len(hits)) if isinstance(page, dict) else len(hits)
                warning = page.get('warning') if isinstance(page, dict) else None
                code = page.get('code') if isinstance(page, dict) else None
                if (next_cursor is not None and (not isinstance(next_cursor, str) or not next_cursor or len(next_cursor) > 2048)
                        or type(fetched_count) is not int or not 0 <= fetched_count <= MAX_PAGE_ROWS
                        or warning is not None and (not isinstance(warning, str) or not warning or len(warning) > 1000)
                        or code is not None and (not isinstance(code, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,79}', code))):
                    raise FetchError('source_error', 'Metadata halaman sumber tidak sesuai kontrak.')
                limited = isinstance(page, dict) and page.get('pagination_limited') is True
                if next_cursor is not None and next_cursor == definition.get('cursor') and not (limited and warning):
                    raise FetchError('source_error', 'Sumber mengulang penanda halaman yang sama; muat ulang pencarian.')
                hits = [job for job in hits if _matches(job, query, filters)]
                status.update(status="ok", count=len(hits), message=f"{len(hits)} kecocokan; {definition['scope']}")
                status.update(next_cursor=next_cursor, fetched_count=fetched_count)
                if warning:
                    status.update(warning=warning, message=status['message'] + ' ' + warning)
                if code:
                    status['code'] = code
                if limited:
                    status['pagination_limited'] = True
                    if not warning:
                        status['message'] += ' Batas penelusuran halaman dari sumber tercapai; persempit filter untuk melanjutkan.'
                for job in hits:
                    key = _canonical_url(job["sourceUrl"])
                    if key not in seen:
                        seen.add(key)
                        jobs.append(job)
            except FetchError as exc:
                status.update(status="error", count=0, message=str(exc), code=exc.code)
            except (ValueError, TypeError, KeyError, IndexError):
                status.update(status="error", count=0, message="Data sumber tidak sesuai kontrak; sumber lain tetap diproses.", code="source_error")
            statuses.append(status)
    return {"ok": True, "jobs": jobs, "sources": statuses, "searched_at": checked,
            "scope": "Sumber terpilih saja; hasil bukan seluruh lowongan di internet. Status dan asal perusahaan belum terkonfirmasi."}


class _MetadataParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.capturing = False
        self.chunks = []
        self.documents = []
        self.password_input = False

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "script" and attributes.get("type", "").lower().split(";")[0] == "application/ld+json":
            self.capturing = True
            self.chunks = []
        if tag == "input" and attributes.get("type", "").lower() == "password":
            self.password_input = True

    def handle_data(self, data):
        if self.capturing:
            self.chunks.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self.capturing:
            try:
                self.documents.append(json.loads("".join(self.chunks)))
            except (ValueError, RecursionError):
                pass  # malformed metadata gives no posting evidence
            self.capturing = False
            self.chunks = []


def _job_postings(documents):
    pending = list(documents)
    postings = []
    visited = 0
    while pending and visited < 5000:
        node = pending.pop()
        visited += 1
        if isinstance(node, list):
            pending.extend(node)
        elif isinstance(node, dict):
            kinds = node.get("@type", [])
            kinds = [kinds] if isinstance(kinds, str) else kinds
            if isinstance(kinds, list) and any(kind == "JobPosting" or kind == "https://schema.org/JobPosting" for kind in kinds):
                postings.append(node)
            graph = node.get("@graph")
            if isinstance(graph, (dict, list)):
                pending.append(graph)
    return postings


def _select_posting(postings, url):
    if len(postings) == 1:
        return postings[0]
    matching = []
    for posting in postings:
        try:
            candidate = _validate_url(urljoin(url, posting.get("url", ""))) if isinstance(posting.get("url"), str) else None
        except FetchError:
            candidate = None
        if candidate and _canonical_url(candidate) == _canonical_url(url):
            matching.append(posting)
    return matching[0] if len(matching) == 1 else None


def apply_portal_detail(job, detail, checked):
    """Use the requested posting's public metadata and application controls only."""
    provider = job.get('providerId')
    parsed = _portal_job_url(provider, detail.get('url')) if provider in ('glints', 'jobstreet') else None
    requested = _portal_job_url(provider, job['sourceUrl']) if parsed else None
    same_job = requested is not None and parsed[1] == requested[1]
    status, deadline = 'unknown', job.get('deadline')
    code = detail.get('http_status') if same_job else None
    if code in (404, 410):
        status = 'closed'
    elif same_job and type(code) is int and 200 <= code < 300:
        content = detail.get('html', '')
        if isinstance(content, str) and len(content.encode('utf-8')) <= MAX_RESPONSE_BYTES:
            metadata = _MetadataParser()
            metadata.feed(content)
            posting = _select_posting(_job_postings(metadata.documents), job['sourceUrl'])
            posting_url = _portal_job_url(provider, posting.get('url')) if posting else None
            same_posting = posting_url is not None and posting_url[1] == parsed[1]
            if posting and (same_posting or (not posting.get('url') and
                    _text(posting.get('title'), 300).casefold() == job['title'].casefold())):
                deadline = posting.get('validThrough') or deadline
            if provider == 'glints':
                parser = _ListingPage()
                parser.feed(content)
                props = parser.data.get('props') if isinstance(parser.data, dict) else None
                cache = props.get('apolloCache') if isinstance(props, dict) else None
                row = cache.get('Job:' + parsed[1]) if isinstance(cache, dict) else None
                if isinstance(row, dict) and row.get('id') == parsed[1]:
                    value = _text(row.get('status'), 50).upper()
                    status = {'OPEN': 'open', 'CLOSED': 'closed', 'EXPIRED': 'expired'}.get(value, 'unknown')
                    deadline = row.get('validThrough') or row.get('expiryDate') or deadline
            notice = detail.get('closed_status')
            if notice in ('closed', 'expired'):
                status = notice
            elif status == 'unknown' and detail.get('can_apply') is True and detail.get('title_matches') is True:
                status = 'open'
    _portal_availability(job, status, deadline, checked)
    messages = {'open': 'Halaman posisi memuat status OPEN atau tombol lamaran aktif untuk judul yang sama.',
                'closed': 'Halaman posisi menyatakan ditutup atau tidak tersedia (HTTP 404/410).',
                'expired': 'Tenggat sumber sudah lewat atau halaman posisi menyatakan kedaluwarsa.',
                'unknown': 'Detail posisi belum dapat mengonfirmasi penerimaan lamaran; tidak termasuk filter Masih dibuka.'}
    message = messages[job['status']] + ' Identitas perusahaan tetap perlu diperiksa sebelum melamar.'
    job['checkedAt'] = checked
    job['evidence'].update(status=job['status'], checked_at=checked, http_status=code,
                           method='local_browser_detail', message=message)
    job['evidenceText'] = message


def verify_source(url):
    """Check public pages, without bypassing robots/login or asserting authenticity."""
    evidence = {"url": "", "checked_at": _stamp(), "http_status": None, "status": "unknown", "origin": "unknown", "posting_present": False}
    try:
        url = _validate_url(url)
        evidence["url"] = url
        result = _fetch(url, accept="text/html,application/xhtml+xml", max_bytes=2 * 1024 * 1024, check_robots=True)
        evidence.update(url=result["url"], http_status=result["status"])
        code = result["status"]
        if code in (404, 410):
            evidence.update(status="closed", message="Halaman sumber tidak tersedia (HTTP 404/410). Periksa apakah lowongan dipindahkan sebelum menyimpulkan penutupan.")
        elif code in (401, 403, 429):
            evidence.update(status="blocked", message=f"Akses dibatasi (HTTP {code}); status lowongan belum dapat dipastikan. Buka sumber secara manual.")
        elif code != 200:
            evidence["message"] = f"HTTP {code}; status lowongan belum dapat dipastikan."
        elif "html" not in result["headers"].get("content-type", "").lower():
            evidence["message"] = "Halaman terbuka, tetapi bukan HTML; periksa dokumen sumber secara manual."
        else:
            parser = _MetadataParser()
            parser.feed(decode_page(result["body"]))
            postings = _job_postings(parser.documents)
            posting = _select_posting(postings, result["url"])
            evidence["posting_present"] = bool(postings)
            if parser.password_input or re.search(r"/(?:login|signin|sign-in)(?:/|$)", urlsplit(result["url"]).path, re.I):
                evidence.update(status="blocked", message="Halaman meminta login; status lowongan belum dapat dipastikan.")
            elif posting:
                evidence.update(posting_present=True, title=_text(posting.get("title"), 300),
                                message="Metadata JobPosting ditemukan. Asal perusahaan dan penerimaan lamaran belum terkonfirmasi.")
                organization = posting.get("hiringOrganization")
                if isinstance(organization, dict):
                    evidence["company"] = _text(organization.get("name"), 200)
                canonical = posting.get("url")
                if isinstance(canonical, str):
                    try:
                        evidence["canonical_url"] = _validate_url(urljoin(result["url"], canonical))
                    except FetchError:
                        pass
                expires = _date(posting.get("validThrough"))
                if expires:
                    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(posting["validThrough"])):
                        expires = datetime.combine(expires.date(), day_time.max, timezone.utc)
                    evidence["valid_through"] = expires.isoformat()
                    if expires < datetime.now(timezone.utc):
                        evidence.update(status="expired", message="Tenggat validThrough pada metadata sumber sudah lewat. Periksa ulang ke perusahaan bila informasi berbeda.")
            elif postings:
                evidence["message"] = "Beberapa metadata JobPosting ditemukan, tetapi posisi untuk URL ini belum dapat dipastikan. Periksa tenggat secara manual."
            else:
                evidence["message"] = "HTTP 200, tetapi metadata JobPosting tidak ditemukan. Ini belum membuktikan lowongan aktif atau keaslian perusahaan."
        return {"ok": True, "evidence": evidence}
    except FetchError as exc:
        evidence.update(message=str(exc), status=exc.code if exc.code.startswith("robots_") else "unknown")
        return {"ok": False, "error": str(exc), "evidence": evidence}
