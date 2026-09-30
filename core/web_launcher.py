"""
web_launcher.py  ─  "크롬 열고 네이버 접속해줘" 같은 브라우저/웹사이트 열기 요청을
LLM을 거치지 않고 바로 처리한다.

왜 따로 두나
- 루미의 LLM(llama3.1)에게는 "웹사이트를 여는" 도구가 구글 캘린더 열기
  (open_calendar_website) 하나뿐이라, "크롬 열고 네이버 접속해줘"를 보내면
  구글 캘린더가 열렸다 (2026-09-30 사용자 제보). 사이트/브라우저 이름은 정해진
  목록으로 충분히 알아낼 수 있으므로 규칙으로 바로 연다 — 빠르고 틀리지 않는다.
- 사이트를 연 뒤 클릭/입력/재생 같은 추가 작업이 필요하면 needs_agent=True로
  알려서 화면 조작 에이전트(core/screen_agent.py)가 이어받게 한다.

기존 기능과 겹치지 않게
- "일정/캘린더/달력"이 들어간 요청은 캘린더 도구에, "최저가/가격/얼마"는 가격
  검색 플러그인에 맡긴다 (여기서는 None 반환).
- "메모장 열어줘"(루미 자체 메모장 플러그인)처럼 브라우저/사이트가 아닌 앱은
  다루지 않는다.

목록에 없는 사이트 ("크롬에서 장안대학교 홈페이지 접속해줘")
- 이름으로 공식 홈페이지 주소를 찾는다: Bing 검색 첫 결과(위키/블로그 제외) →
  실패하면 네이버 검색 결과에서 가장 많이 나온 사이트 → 그래도 없으면 네이버
  검색 결과 화면을 연다. 이때 사이트 이름(검색어)만 검색 엔진에 보내진다.
- 네트워크를 쓰므로 WebOpenWorker(QThread)에서 처리한다 (화면 멈춤 방지).
"""
import base64
import os
import re
import subprocess
import sys
import webbrowser
from collections import Counter
from urllib.parse import parse_qs, quote_plus, urlparse

from PyQt6.QtCore import QThread, pyqtSignal

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform == "win32"

# 브라우저: 사용자가 부르는 이름 → (표시 이름, macOS 앱 이름, Windows 실행 파일)
BROWSERS = {
    "크롬":       ("크롬", "Google Chrome", "chrome.exe"),
    "chrome":     ("크롬", "Google Chrome", "chrome.exe"),
    "엣지":       ("엣지", "Microsoft Edge", "msedge.exe"),
    "edge":       ("엣지", "Microsoft Edge", "msedge.exe"),
    "사파리":     ("사파리", "Safari", None),
    "safari":     ("사파리", "Safari", None),
    "웨일":       ("웨일", "Naver Whale", "whale.exe"),
    "whale":      ("웨일", "Naver Whale", "whale.exe"),
    "파이어폭스": ("파이어폭스", "Firefox", "firefox.exe"),
    "firefox":    ("파이어폭스", "Firefox", "firefox.exe"),
}

# 사이트: 이름 → (표시 이름, 주소, 검색 주소 또는 None)
SITES = {
    "네이버 지도": ("네이버 지도", "https://map.naver.com", "https://map.naver.com/p/search/{q}"),
    "네이버 메일": ("네이버 메일", "https://mail.naver.com", None),
    "네이버":     ("네이버", "https://www.naver.com", "https://search.naver.com/search.naver?query={q}"),
    "naver":      ("네이버", "https://www.naver.com", "https://search.naver.com/search.naver?query={q}"),
    "구글 지도":  ("구글 지도", "https://maps.google.com", "https://www.google.com/maps/search/{q}"),
    "구글 드라이브": ("구글 드라이브", "https://drive.google.com", None),
    "지메일":     ("지메일", "https://mail.google.com", None),
    "gmail":      ("지메일", "https://mail.google.com", None),
    "구글":       ("구글", "https://www.google.com", "https://www.google.com/search?q={q}"),
    "google":     ("구글", "https://www.google.com", "https://www.google.com/search?q={q}"),
    "유튜브":     ("유튜브", "https://www.youtube.com", "https://www.youtube.com/results?search_query={q}"),
    "youtube":    ("유튜브", "https://www.youtube.com", "https://www.youtube.com/results?search_query={q}"),
    "다음":       ("다음", "https://www.daum.net", "https://search.daum.net/search?q={q}"),
    "daum":       ("다음", "https://www.daum.net", "https://search.daum.net/search?q={q}"),
    "쿠팡":       ("쿠팡", "https://www.coupang.com", "https://www.coupang.com/np/search?q={q}"),
    "나무위키":   ("나무위키", "https://namu.wiki", "https://namu.wiki/Search?q={q}"),
    "위키백과":   ("위키백과", "https://ko.wikipedia.org", "https://ko.wikipedia.org/w/index.php?search={q}"),
    "깃허브":     ("깃허브", "https://github.com", "https://github.com/search?q={q}"),
    "github":     ("깃허브", "https://github.com", "https://github.com/search?q={q}"),
    "인스타그램": ("인스타그램", "https://www.instagram.com", None),
    "인스타":     ("인스타그램", "https://www.instagram.com", None),
    "페이스북":   ("페이스북", "https://www.facebook.com", None),
    "넷플릭스":   ("넷플릭스", "https://www.netflix.com", None),
    "노션":       ("노션", "https://www.notion.so", None),
    "파파고":     ("파파고", "https://papago.naver.com", None),
}

_OPEN_INTENT = re.compile(r"열어|열고|열기|열어줘|켜줘|켜 줘|켜고|실행|접속|들어가|띄워|가줘|가 줘|이동|검색|찾아")
_URL = re.compile(r"(https?://[^\s]+|(?:www\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|net|org|co\.kr|kr|io|ai|dev|me|wiki)(?:/[^\s]*)?)",
                  re.IGNORECASE)
# 사이트 이름이 목록에 없을 때 "이걸 열어달라"는 신호가 되는 말
_HOMEPAGE_HINT = re.compile(r"홈페이지|사이트|웹사이트|접속|들어가")
_SITE_WORD = re.compile(r"홈페이지|웹사이트|사이트")
_OPEN_VERB = re.compile(r"열어|열고|열기|접속|들어가|띄워|가줘|가 줘|이동")
_SECURITY_WORDS = re.compile(r"보안|악성|차단|방화벽|포트|네트워크|위험|해킹|기록")
# 이름을 뽑을 때 지울 말 (길이순으로 지운다)
_FILLER = sorted([
    "공식 홈페이지", "공식홈페이지", "홈페이지", "공식 사이트", "공식사이트", "웹사이트", "사이트", "공식",
    "접속해줘", "접속해 줘", "접속해서", "접속하고", "접속한 뒤", "접속해", "접속하기", "접속",
    "들어가줘", "들어가 줘", "들어가서", "들어가고", "들어가",
    "열어줘", "열어 줘", "열어서", "열고서", "열고", "열어", "켜고", "켜서", "켜줘",
    "실행하고", "실행해서", "실행해", "띄워줘", "띄워서", "띄워",
    "이동해줘", "이동", "가줘", "좀", "한번", "주세요", "줘",
], key=len, reverse=True)
# 공식 홈페이지 후보에서 뺄 곳 (정보/커뮤니티 사이트)
_NOT_OFFICIAL = ("namu.wiki", "wikipedia.org", "tistory.com", "blog.naver.com", "cafe.naver.com",
                 "youtube.com", "instagram.com", "facebook.com", "brunch.co.kr", "velog.io",
                 "kin.naver.com", "dcinside.com", "grandculture.net", "megastudy.net", "bing.com")
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
            "Accept-Language": "ko-KR,ko;q=0.9"}

# 다른 기능이 맡아야 하는 요청
_OTHER_FEATURES = re.compile(r"일정|캘린더|달력|최저가|가격|얼마")
# 사이트를 연 뒤 화면에서 더 해야 하는 일 → 화면 조작 에이전트로
_NEEDS_AGENT = re.compile(r"클릭|눌러|로그인|입력|써줘|작성|다운로드|재생|틀어|구독|좋아요|댓글|보내|장바구니")


class BrowserNotFound(Exception):
    pass


# 사이트 이름 바로 뒤에 올 수 있는 말 — "다음주", "다음에"처럼 이름이 다른 단어의
# 일부인 경우를 걸러낸다
_AFTER_SITE = r"(?=$|[\s,.!?]|에서|으로|로|을|를|에\s|사이트|홈페이지|접속|열|켜|검색|들어)"


def _site_mentioned(key: str, low: str) -> bool:
    if key == "다음":   # 일상어와 겹쳐서 "사이트로서의 다음"이 분명할 때만
        return re.search(r"다음\s*(?:사이트|홈페이지|포털)|다음(?:에서|을|를)\s*(?:열|켜|접속|검색|들어)|다음\s+(?:열어|켜|접속)", low) is not None
    return re.search(re.escape(key) + _AFTER_SITE, low) is not None


def parse_open_request(text: str):
    """열기 요청이면 dict, 아니면 None.
    {"browser": 키 또는 None, "site": 키 또는 None, "url": 열 주소 또는 None,
     "query": 검색어 또는 None, "needs_agent": bool}"""
    t = (text or "").strip()
    low = t.lower()
    if not t or _OTHER_FEATURES.search(t) or not _OPEN_INTENT.search(t):
        return None

    browser = next((k for k in sorted(BROWSERS, key=len, reverse=True) if k in low), None)
    site = next((k for k in sorted(SITES, key=len, reverse=True) if _site_mentioned(k, low)), None)
    m = _URL.search(t)
    explicit_url = None
    if m and not site:
        explicit_url = m.group(1)
        if not explicit_url.lower().startswith("http"):
            explicit_url = "https://" + explicit_url
    target = None
    if not site and not explicit_url and _wants_unlisted_site(t, browser):
        target = _extract_target(t, browser)
    if not (browser or site or explicit_url or target):
        return None

    needs_agent = bool(_NEEDS_AGENT.search(t))
    query = None
    if "검색" in t and not needs_agent:
        query = _extract_query(t, browser, site)

    url = explicit_url
    if site:
        _, home, search = SITES[site]
        url = search.format(q=quote_plus(query)) if (query and search) else home
    elif query:   # 사이트 없이 "크롬에서 날씨 검색해줘" → 구글 검색
        url = "https://www.google.com/search?q=" + quote_plus(query)
        site = "구글"
    if target and not query:
        url = None   # WebOpenWorker가 이름으로 주소를 찾는다
    else:
        target = None
    return {"browser": browser, "site": site, "url": url, "query": query,
            "target": target, "needs_agent": needs_agent}


def _wants_unlisted_site(t: str, browser) -> bool:
    """목록에 없는 사이트를 열어달라는 말인가. "네트워크 접속 기록 보여줘",
    "악성 사이트 찾아줘" 같은 보안 기능 요청을 가로채지 않도록 좁게 본다:
    (브라우저를 말했거나 "홈페이지/사이트"라는 말이 있고) + 여는 동사가 있어야 한다."""
    if _SECURITY_WORDS.search(t) or not _OPEN_VERB.search(t):
        return False
    return bool(browser) and _HOMEPAGE_HINT.search(t) is not None or _SITE_WORD.search(t) is not None


def _extract_target(text: str, browser) -> str:
    """"크롬에서 장안대학교 홈페이지 접속해줘" → "장안대학교" """
    t = text
    if browser:
        t = re.sub(re.escape(browser) + r"\s*(?:에서|으로|로|을|를|앱|브라우저)?", " ", t, flags=re.IGNORECASE)
    for word in _FILLER:
        t = t.replace(word, " ")
    t = re.sub(r"(?<=\S)(?:의|에|을|를|으로|로)(?=\s|$)", " ", t)   # 이름 뒤 조사
    # 연결 표현을 지우고 남은 한 글자 찌꺼기("접속해서"→"서" 등) 제거 — 2026-09-30
    # "크롬 접속해서 장안대학교…"가 "서 장안대학교"로 검색돼 엉뚱한 사이트가 열렸다
    words = [w for w in t.split() if not (len(w) == 1 and w in "서고해줘좀")]
    return " ".join(words).strip(" ,.!?") or None


def find_homepage(name: str, timeout: float = 6.0):
    """이름 → 공식 홈페이지 주소 (못 찾으면 None)."""
    for finder in (_bing_first_result, _naver_most_common_site):
        try:
            url = finder(name, timeout)
            if url:
                return url
        except Exception as e:
            print(f"[웹 열기] {finder.__name__} 실패: {e}")
    return None


def _is_official_candidate(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return bool(host) and url.startswith("http") and not any(bad in host for bad in _NOT_OFFICIAL)


def _bing_first_result(name: str, timeout: float):
    import requests
    from bs4 import BeautifulSoup
    r = requests.get("https://www.bing.com/search", params={"q": name, "setlang": "ko", "cc": "KR"},
                     headers=_HEADERS, timeout=timeout)
    soup = BeautifulSoup(r.text, "html.parser")
    candidates = []
    for a in soup.select("li.b_algo h2 a"):
        url = _unwrap_bing(a.get("href", ""))
        if _is_official_candidate(url):
            candidates.append((a.get_text(" ", strip=True), url))
    if not candidates:
        return None
    # 제목에 찾는 이름이 들어간 결과를 먼저 (검색어가 조금 틀려도 엉뚱한 사이트를 덜 연다)
    key = re.sub(r"\s+", "", name).lower()
    for title, url in candidates:
        if key and key in re.sub(r"\s+", "", title).lower():
            return url
    return candidates[0][1]


def _unwrap_bing(href: str) -> str:
    """Bing 추적 링크(bing.com/ck/a?...&u=a1<base64>)에서 실제 주소를 꺼낸다."""
    if "bing.com/ck/" not in href:
        return href
    u = parse_qs(urlparse(href).query).get("u", [""])[0]
    if u.startswith("a1"):
        u = u[2:]
        try:
            return base64.urlsafe_b64decode(u + "=" * (-len(u) % 4)).decode("utf-8", "ignore")
        except ValueError:
            return ""
    return ""


def _naver_most_common_site(name: str, timeout: float):
    import requests
    from bs4 import BeautifulSoup
    r = requests.get("https://search.naver.com/search.naver", params={"query": name + " 홈페이지"},
                     headers=_HEADERS, timeout=timeout)
    soup = BeautifulSoup(r.text, "html.parser")
    urls = [a["href"] for a in soup.find_all("a", href=True)
            if _is_official_candidate(a["href"]) and "naver" not in urlparse(a["href"]).netloc]
    if not urls:
        return None
    host, _ = Counter(urlparse(u).netloc for u in urls).most_common(1)[0]
    return f"{urlparse(next(u for u in urls if urlparse(u).netloc == host)).scheme}://{host}/"


class WebOpenWorker(QThread):
    """이름으로 주소를 찾아(네트워크) 연다. 끝나면 사용자에게 할 말을 보낸다."""
    finished_open = pyqtSignal(str)

    def __init__(self, req: dict, parent=None):
        super().__init__(parent)
        self.req = req

    def run(self):
        req = dict(self.req)
        name = req.pop("target")
        url = find_homepage(name)
        try:
            if url:
                req["url"] = url
                message = open_request(req, label=f"{name} 홈페이지")
            else:
                req["url"] = "https://search.naver.com/search.naver?query=" + quote_plus(name + " 홈페이지")
                message = open_request(req, label=f"'{name}' 검색 결과") + \
                    " (공식 홈페이지 주소를 찾지 못해서 검색 결과를 열었어요.)"
        except Exception as e:
            print(f"[웹 열기] 오류: {e}")
            message = "❌ 브라우저를 열지 못했어요. 잠시 후 다시 시도해주세요."
        self.finished_open.emit(message)


def _extract_query(text: str, browser, site) -> str:
    """"크롬 열고 네이버에서 오늘 날씨 검색해줘" → "오늘 날씨" """
    before = text.split("검색", 1)[0]
    # 사이트/브라우저 이름과 그 뒤 조사, "열고/켜고/접속해서" 같은 연결 표현을 지운다
    for name in filter(None, (site, browser)):
        before = re.sub(re.escape(name) + r"\s*(?:에서|으로|로|을|를|에|앱)?", " ", before, flags=re.IGNORECASE)
    before = re.sub(r"(?:열고|열어서|켜고|켜서|접속해서|접속하고|들어가서|실행하고|실행해서)", " ", before)
    before = re.sub(r"\s*(?:을|를)\s*$", "", before.strip())
    return re.sub(r"\s+", " ", before).strip(" ,.") or None


def open_request(req: dict, label: str = None) -> str:
    """요청대로 열고 사용자에게 보여줄 문장을 돌려준다."""
    url = req.get("url")
    browser = req.get("browser")
    site_label = SITES[req["site"]][0] if req.get("site") else None
    what = label or (f"'{req['query']}' {site_label} 검색 결과" if req.get("query")
                     else site_label or url or "")

    if browser:
        label = BROWSERS[browser][0]
        try:
            _launch_browser(browser, url)
            return f"{label}에서 {what}를 열었어요." if what else f"{label}를 실행했어요."
        except BrowserNotFound:
            if not url:
                return f"{label}가 이 컴퓨터에 설치돼 있지 않은 것 같아요."
            webbrowser.open(url, new=2)
            return f"{label}를 찾지 못해서 기본 브라우저로 {what}를 열었어요."

    webbrowser.open(url, new=2)
    return f"{what}를 열었어요."


def _launch_browser(browser: str, url):
    _, mac_app, win_exe = BROWSERS[browser]
    if IS_MAC:
        cmd = ["open", "-a", mac_app] + ([url] if url else [])
        if subprocess.run(cmd, capture_output=True, timeout=15).returncode != 0:
            raise BrowserNotFound(browser)
    elif IS_WIN:
        if not win_exe:
            raise BrowserNotFound(browser)
        try:
            # 셸을 거치지 않고 Windows에 등록된 앱 경로(App Paths)로 실행
            os.startfile(win_exe, arguments=url or "")
        except OSError:
            raise BrowserNotFound(browser)
    else:
        exe = {"크롬": "google-chrome", "파이어폭스": "firefox"}.get(BROWSERS[browser][0])
        if not exe:
            raise BrowserNotFound(browser)
        try:
            subprocess.Popen([exe] + ([url] if url else []))
        except OSError:
            raise BrowserNotFound(browser)
