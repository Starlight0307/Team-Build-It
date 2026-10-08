# -*- coding: utf-8 -*-
"""
malware_detection.py와 realtime_monitor.py는 플러그인끼리 import하지 않는 관례 때문에
의심 프로세스 판정 기준을 각자 복사해 둔다. 한쪽만 고치면 "지금 점검"과 "실시간 감시"의
판정이 달라지므로, 두 표가 같은지 여기서 검사한다(2026-10-08 이름 사칭 판정 추가 때 도입).
"""
import os
import sys

import pytest

import plugins.malware_detection as md
import plugins.realtime_monitor as rm


def test_suspicious_keywords_match():
    assert md.SUSPICIOUS_KEYWORDS == rm._SUSPICIOUS_KEYWORDS


def test_system_process_dirs_match():
    assert md.SYSTEM_PROCESS_DIRS == rm._SYSTEM_PROCESS_DIRS


def test_app_whitelist_matches():
    assert md.APP_PROCESS_WHITELIST == rm._APP_PROCESS_WHITELIST


def test_temp_markers_match():
    assert md.TEMP_DIR_MARKERS == rm._TEMP_DIR_MARKERS


def test_keyword_categories_match():
    assert md._KEYWORD_CATEGORY == rm._KEYWORD_CATEGORY


@pytest.mark.parametrize("name, exe", [
    ("svchost.exe", "C:\\Windows\\System32\\svchost.exe"),
    ("svchost.exe", "C:\\Users\\a\\AppData\\Roaming\\svchost.exe"),
    ("csrss.exe", ""),
    ("python.exe", "C:\\Users\\a\\AppData\\Local\\Temp\\python.exe"),
    ("python.exe", "C:\\Python311\\python.exe"),
    ("other.exe", "C:\\x\\other.exe"),
])
def test_whitelist_verdicts_match(monkeypatch, name, exe):
    monkeypatch.setattr(md, "_system_root", lambda: "C:\\Windows")
    monkeypatch.setattr(rm, "_system_root", lambda: "C:\\Windows")
    assert md._whitelist_verdict(name, exe) == rm._whitelist_verdict(name, exe)


def test_whitelist_names_are_lowercase():
    """판정할 때 이름을 소문자로 바꿔 비교하므로, 표에 대문자가 있으면 절대 일치하지 않는다."""
    for table in (md.SYSTEM_PROCESS_DIRS, md.APP_PROCESS_WHITELIST):
        assert all(n == n.lower() for n in table)


def test_user_drop_dir_markers_match():
    assert md.USER_DROP_DIR_MARKERS == rm._USER_DROP_DIR_MARKERS


@pytest.mark.skipif(sys.platform != "win32", reason="Windows API(GetSystemWindowsDirectoryW) 확인용")
@pytest.mark.parametrize("module", [md, rm])
def test_system_root_ignores_tampered_environment(monkeypatch, module):
    """SystemRoot 환경변수를 바꿔도 실제 Windows 폴더를 기준으로 판정해야 한다(ChatGPT 검수 1차)."""
    monkeypatch.setenv("SystemRoot", "C:\\FakeWindows")
    monkeypatch.setenv("WINDIR", "C:\\FakeWindows")

    root = module._system_root()

    assert root.lower() != "c:\\fakewindows"
    assert os.path.isfile(os.path.join(root, "System32", "kernel32.dll"))
