"""core/web_launcher.py — "크롬 열고 네이버 접속해줘"가 구글 캘린더를 열던 버그(2026-09-30)의 회귀 테스트."""
import pytest

from core import web_launcher
from core.web_launcher import parse_open_request


@pytest.mark.parametrize("text, browser, url", [
    ("크롬을 열고 네이버를 접속해줘", "크롬", "https://www.naver.com"),
    ("크롬 열고 네이버 접속해줘", "크롬", "https://www.naver.com"),
    ("네이버 열어줘", None, "https://www.naver.com"),
    ("유튜브 켜줘", None, "https://www.youtube.com"),
    ("엣지로 구글 들어가줘", "엣지", "https://www.google.com"),
    ("크롬 켜줘", "크롬", None),
    ("github.com 접속해줘", None, "https://github.com"),
    ("크롬에서 https://example.com/a 열어줘", "크롬", "https://example.com/a"),
    ("다음 사이트 열어줘", None, "https://www.daum.net"),
    ("네이버 지도 열어줘", None, "https://map.naver.com"),   # "네이버"보다 긴 이름 우선
])
def test_open_requests(text, browser, url):
    req = parse_open_request(text)
    assert req is not None
    assert req["browser"] == browser
    assert req["url"] == url
    assert req["needs_agent"] is False


@pytest.mark.parametrize("text, url", [
    ("크롬 열고 네이버에서 오늘 날씨 검색해줘", "https://search.naver.com/search.naver?query=%EC%98%A4%EB%8A%98+%EB%82%A0%EC%94%A8"),
    ("유튜브에서 아이유 검색해줘", "https://www.youtube.com/results?search_query=%EC%95%84%EC%9D%B4%EC%9C%A0"),
    ("크롬에서 파이썬 설치 방법 검색해줘", "https://www.google.com/search?q=%ED%8C%8C%EC%9D%B4%EC%8D%AC+%EC%84%A4%EC%B9%98+%EB%B0%A9%EB%B2%95"),
])
def test_search_requests(text, url):
    assert parse_open_request(text)["url"] == url


def test_extra_actions_go_to_screen_agent():
    assert parse_open_request("유튜브 열고 아이유 노래 재생해줘")["needs_agent"] is True
    assert parse_open_request("네이버 접속해서 로그인 버튼 눌러줘")["needs_agent"] is True


@pytest.mark.parametrize("text", [
    "캘린더 웹사이트 열어줘",          # 캘린더 도구가 처리
    "구글 캘린더 열어줘",
    "다나와에서 에어팟 최저가 검색해줘",  # 가격 검색 플러그인이 처리
    "메모장 열어줘",                   # 루미 메모장 플러그인
    "다음주 일정 알려줘",
    "다음에 크롬 열어줄래",            # "다음"은 사이트가 아님 → 크롬만
    "오늘 유튜브 몇 시간 봤어",         # 앱 사용 통계
    "네이버 뉴스 요약해줘",            # 열어달라는 말이 없음
])
def test_not_hijacking_other_features(text):
    req = parse_open_request(text)
    if text == "다음에 크롬 열어줄래":
        assert req["browser"] == "크롬" and req["site"] is None
    else:
        assert req is None


def test_open_request_falls_back_to_default_browser(monkeypatch):
    opened = []
    monkeypatch.setattr(web_launcher, "_launch_browser",
                        lambda b, u: (_ for _ in ()).throw(web_launcher.BrowserNotFound(b)))
    monkeypatch.setattr(web_launcher.webbrowser, "open", lambda url, new=0: opened.append(url))
    msg = web_launcher.open_request(parse_open_request("웨일로 네이버 열어줘"))
    assert opened == ["https://www.naver.com"]
    assert "기본 브라우저" in msg


def test_open_request_message(monkeypatch):
    launched = []
    monkeypatch.setattr(web_launcher, "_launch_browser", lambda b, u: launched.append((b, u)))
    msg = web_launcher.open_request(parse_open_request("크롬을 열고 네이버를 접속해줘"))
    assert launched == [("크롬", "https://www.naver.com")]
    assert msg == "크롬에서 네이버를 열었어요."


@pytest.mark.parametrize("text, target, browser", [
    ("크롬에서 장안대학교 홈페이지 접속해줘", "장안대학교", "크롬"),
    ("장안대학교 홈페이지 열어줘", "장안대학교", None),
    ("크롬 열고 장안대학교 들어가줘", "장안대학교", "크롬"),
    ("엣지로 한국장학재단 사이트 띄워줘", "한국장학재단", "엣지"),
    ("수원시청 공식 홈페이지 접속", "수원시청", None),
    ("크롬 접속해서 장안대학교 홈페이지 접속해줘", "장안대학교", "크롬"),   # "서 장안대학교" 버그
    ("크롬 열어서 장안대학교 홈페이지 들어가줘", "장안대학교", "크롬"),
    ("엣지 켜고 한국장학재단 사이트 열어줘", "한국장학재단", "엣지"),
])
def test_unlisted_site_needs_lookup(text, target, browser):
    req = parse_open_request(text)
    assert req["target"] == target
    assert req["browser"] == browser
    assert req["url"] is None


@pytest.mark.parametrize("text", [
    "네트워크 접속 기록 보여줘",   # 네트워크 보안 플러그인
    "악성 사이트 찾아줘",          # 악성코드 탐지
    "크롬 켜줘",                  # 브라우저만
    "크롬 열고 네이버 접속해줘",   # 목록에 있는 사이트
])
def test_no_lookup_for_other_requests(text):
    req = parse_open_request(text)
    assert req is None or req["target"] is None


def test_unwrap_bing_tracking_link():
    href = ("https://www.bing.com/ck/a?!&&p=abc&ptn=3&ver=2&hsh=4"
            "&u=a1aHR0cHM6Ly93d3cuamFuZ2FuLmFjLmtyLw&ntb=1")
    assert web_launcher._unwrap_bing(href) == "https://www.jangan.ac.kr/"


def test_lookup_falls_back_to_search_page(monkeypatch):
    opened = []
    monkeypatch.setattr(web_launcher, "find_homepage", lambda name: None)
    monkeypatch.setattr(web_launcher, "_launch_browser", lambda b, u: opened.append(u))
    worker = web_launcher.WebOpenWorker(parse_open_request("크롬에서 없는학교 홈페이지 접속해줘"))
    messages = []
    worker.finished_open.connect(messages.append)
    worker.run()
    assert opened and opened[0].startswith("https://search.naver.com/")
    assert "찾지 못해서" in messages[0]


# ── 2026-10-02 음성 "크롬에서 장안대학교 홈페이지 들어가 줘" → "들어가죠" 받아쓰기 버그 ──
@pytest.mark.parametrize("text", [
    "크롬에서 장안대학교 들어가죠.", "크롬에서 장안대학교 홈페이지 들어가줘요",
    "크롬에서 장안대학교 홈페이지 들어가 줘", "장안대학교 홈페이지 열어주세요",
])
def test_verb_endings_do_not_leak_into_search_query(text):
    assert parse_open_request(text)["target"] == "장안대학교"


def test_lookup_never_opens_unrelated_first_result(monkeypatch):
    """제목에 찾는 이름이 없는 결과는 열지 않는다 (예전: "장안대학교 죠" → 홈택스)."""
    seen = []
    def fake_exact(name, timeout):
        seen.append(name)
        return "https://www.jangan.ac.kr/" if name == "장안대학교" else None
    monkeypatch.setattr(web_launcher, "_find_exact", fake_exact)
    assert web_launcher.find_homepage("메소 장안대학교") == "https://www.jangan.ac.kr/"
    assert seen == ["메소 장안대학교", "장안대학교"]          # 정확히 안 맞으면 가장 긴 낱말로 다시
    monkeypatch.setattr(web_launcher, "_find_exact", lambda n, t: None)
    assert web_launcher.find_homepage("없는학교") is None


def test_open_request_without_url_opens_search_page(monkeypatch):
    opened = []
    monkeypatch.setattr(web_launcher.webbrowser, "open", lambda url, new=0: opened.append(url))
    web_launcher.open_request({"browser": None, "site": None, "url": None, "query": None, "target": "없는학교"})
    assert opened and opened[0].startswith("https://search.naver.com/")
