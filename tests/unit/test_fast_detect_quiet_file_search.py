# -*- coding: utf-8 -*-
"""방해금지 상태 빠른 감지 + 파일 검색 인자 근거 판정 (2026-11-12). 모델은 쓰지 않는다."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

import core.ai_worker as aw
from core import reply_actions as ra

NONE_RAW = ("[🔕 묵음 시간대]\n설정된 묵음 시간대가 없어요. "
            "'밤 10시부터 아침 7시까지는 알림 묵음으로 해줘'처럼 알려 주시면 설정해드려요.")


def worker(text):
    calls = []

    def get_quiet_hours_status():
        calls.append(1)
        return NONE_RAW
    w = aw.AIWorker(text, [], [get_quiet_hours_status], "s")
    out = []
    w.response_ready.connect(out.append)
    return w, calls, out


@pytest.fixture(autouse=True)
def _c():
    ra.clear()
    yield
    ra.clear()


@pytest.mark.parametrize("q", ["방해금지 상태 알려줘", "방해금지 상태 알려줘?", "지금 묵음 설정돼 있어?", "방해 금지 모드 켜져 있어?",
                               "묵음 시간대 확인해줘", "방해금지 설정된 거 있어?", "방해금지 어때?"])
def test_status_questions_are_intercepted(q):
    with aw.offer_context("s", "u"):
        w, calls, out = worker(q)
        assert w._fast_quiet_status() is True
    assert calls == [1]
    assert "지금 설정된 방해금지(묵음) 시간대는 없어요. 오후 10시부터 오전 7시까지로 설정해 드릴까요?" in out[0]


@pytest.mark.parametrize("q", ["방해금지 설정해줘", "묵음 해제해줘", "밤 10시부터 아침 7시까지 방해금지", "방해금지 꺼줘", "방해금지 켜줘",
                               "방해금지 취소해줘", "방해금지 시간 바꿔줘", "방해금지 모드가 뭐야?", "방해금지 켜져 있으면 알려줘",
                               "오후 11시 묵음 설정 알려줘", "날씨 알려줘", "일정 상태 알려줘", "묵음으로 해줘",
                               "볼륨 묵음 상태 알려줘", "소리 묵음 확인해줘", "스피커 묵음 상태", "음량 음소거 상태", "마이크 음소거 상태 알려줘",
                               "알림음 음소거 상태", "방해금지 상태 알려주고 일정도 알려줘", "방해금지 상태 확인하고 날씨도", "묵음 상태 그리고 타이머",
                               "방해금지 상태랑 일정 알려줘", "방해금지 상태도 알려줘 메모도", "방해금지 상태 알려줘 할 일도"])
def test_writes_and_others_not_intercepted(q):
    w, calls, out = worker(q)
    assert w._fast_quiet_status() is False
    assert calls == [] and out == []


G = aw._ground_file_search_args


def test_invented_kind_removed():
    assert G({"file_type": "pdf", "period": "this_week", "time_basis": "created"}, "이번주에 받은 파일 찾아줘") == \
        {"period": "this_week", "time_basis": "created"}


@pytest.mark.parametrize("kind,text", [("pdf", "지난주 PDF 찾아줘"), ("pdf", "피디에프 파일 찾아줘"), ("이미지", "어제 받은 사진 찾아줘"),
                                       ("jpg", "사진 찾아줘"), ("동영상", "영상 파일 찾아줘"), ("엑셀", "엑셀 찾아줘"),
                                       ("ppt", "발표 자료 ppt 찾아줘"), ("csv", "csv 파일 찾아줘"), (".hwp", "hwp 찾아줘"),
                                       ("py", "파이썬 파일 찾아줘"), ("py", "코드 파일 찾아줘"), ("txt", "텍스트 파일 찾아줘"),
                                       ("png", "캡처 파일 찾아줘"), ("png", "스크린샷 찾아줘"), ("js", "자바스크립트 파일 찾아줘"),
                                       ("xlsx", "표 파일 찾아줘"), ("xlsx", "스프레드시트 찾아줘"), ("hwp", "한글 문서 찾아줘"),
                                       ("doc", "한글 문서 찾아줘"), ("images", "사진 찾아줘"), ("image", "이미지 찾아줘"),
                                       ("photo", "사진 찾아줘"), ("document", "문서 찾아줘"), ("video", "영상 찾아줘"),
                                       ("audio", "음악 파일 찾아줘"), ("music", "노래 찾아줘"), ("archive", "압축 파일 찾아줘"),
                                       ("zip", "zip 파일 찾아줘"), ("code", "소스 파일 찾아줘"), ("pdf", "문서 찾아줘"),
                                       ("docx", "문서 찾아줘"), ("txt", "문서 찾아줘"), ("mov", "mov 영상 찾아줘")])
def test_grounded_kind_kept(kind, text):
    assert G({"file_type": kind}, text).get("file_type") == kind


@pytest.mark.parametrize("kind,text", [("pdf", "받은 파일 찾아줘"), ("pdf", "최근 파일 찾아줘"), ("이미지", "이번주 파일 찾아줘"),
                                       ("동영상", "사진 찾아줘"), ("zip", "파일 찾아줘"), ("images", "파일 찾아줘"), ("video", "문서 찾아줘"),
                                       ("mov", "movie 파일 찾아줘"), ("avi", "aviation 자료 찾아줘"), ("zip", "zipcode 파일 찾아줘"),
                                       ("py", "happy 파일 찾아줘"), ("exe", "파일 찾아줘")])
def test_ungrounded_kind_removed(kind, text):
    assert "file_type" not in G({"file_type": kind}, text)


def test_keyword_grounding():
    assert G({"keyword": "보고서"}, "보고서 파일 찾아줘")["keyword"] == "보고서"
    assert G({"keyword": "Team-BuildIt"}, "team buildit 폴더 파일 찾아줘")["keyword"] == "Team-BuildIt"
    assert G({"keyword": "보고서 최종"}, "보고서 찾아줘")["keyword"] == "보고서"
    assert "keyword" not in G({"keyword": "계약서"}, "이번주에 받은 파일 찾아줘")
    assert G({"keyword": "x"}, "") == {}


def test_other_args_untouched():
    a = {"period": "last_week", "time_basis": "modified", "folder": "C:/x", "recent_days": 3}
    assert G(a, "아무 말") == a
