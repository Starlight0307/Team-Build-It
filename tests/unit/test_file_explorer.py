# -*- coding: utf-8 -*-
"""
plugins/file_explorer.py — 2026-09-29 실사용 재현 공백 "이 파일이 있는
폴더를 열어줘"에 대응. subprocess.run/os.startfile을 monkeypatch로 스텁해서
실제로 탐색기 창을 띄우지 않고 오프라인으로 검증한다.
"""
import os

import pytest

import plugins.file_explorer as fe


def test_empty_path_asks_for_specifics():
    assert "알려주세요" in fe.open_file_location("")


def test_nonexistent_path_is_rejected_safely(tmp_path, monkeypatch):
    """LLM이 지어낸 경로거나 오타면 아무 창도 안 띄우고 정직하게 실패해야
    한다 — 이게 추가 방어선 역할도 한다."""
    calls = []
    monkeypatch.setattr(fe.subprocess, "run", lambda *a, **kw: calls.append(a))
    result = fe.open_file_location(str(tmp_path / "없는파일.txt"))
    assert "찾을 수 없어요" in result
    assert calls == []  # 아무 것도 실행되지 않았어야 함


def test_opens_folder_directly_on_windows(tmp_path, monkeypatch):
    folder = tmp_path / "어떤폴더"
    folder.mkdir()
    calls = []
    monkeypatch.setattr(fe.platform, "system", lambda: "Windows")
    monkeypatch.setattr(fe.os, "startfile", lambda p: calls.append(p), raising=False)
    result = fe.open_file_location(str(folder))
    assert calls == [str(folder)]
    assert "열었어요" in result


def test_selects_file_in_parent_folder_on_windows(tmp_path, monkeypatch):
    """파일이면 그 파일을 선택한 채로 부모 폴더를 열어야 한다(explorer
    /select, 방식) — 폴더를 통째로 여는 것과는 다른 동작."""
    f = tmp_path / "보고서.pdf"
    f.write_text("dummy")
    calls = []
    monkeypatch.setattr(fe.platform, "system", lambda: "Windows")
    monkeypatch.setattr(fe.subprocess, "run", lambda *a, **kw: calls.append(a))
    result = fe.open_file_location(str(f))
    assert calls and calls[0][0] == ["explorer", f"/select,{f}"]
    assert "열었어요" in result


def test_strips_surrounding_quotes(tmp_path, monkeypatch):
    folder = tmp_path / "폴더"
    folder.mkdir()
    calls = []
    monkeypatch.setattr(fe.platform, "system", lambda: "Windows")
    monkeypatch.setattr(fe.os, "startfile", lambda p: calls.append(p), raising=False)
    fe.open_file_location(f'"{folder}"')
    assert calls == [str(folder)]


def test_handles_launch_exception_gracefully(tmp_path, monkeypatch):
    folder = tmp_path / "폴더"
    folder.mkdir()
    monkeypatch.setattr(fe.platform, "system", lambda: "Windows")

    def _boom(p):
        raise OSError("실행 실패")
    monkeypatch.setattr(fe.os, "startfile", _boom, raising=False)
    result = fe.open_file_location(str(folder))
    assert "문제가 발생했어요" in result


def test_never_executes_the_file_itself(tmp_path, monkeypatch):
    """모듈 docstring의 경계 — 파일을 직접 실행(os.startfile(file))하지
    않고, 항상 explorer /select로 "위치만" 연다."""
    f = tmp_path / "프로그램.exe"
    f.write_text("dummy")
    startfile_calls = []
    run_calls = []
    monkeypatch.setattr(fe.platform, "system", lambda: "Windows")
    monkeypatch.setattr(fe.os, "startfile", lambda p: startfile_calls.append(p), raising=False)
    monkeypatch.setattr(fe.subprocess, "run", lambda *a, **kw: run_calls.append(a))
    fe.open_file_location(str(f))
    assert startfile_calls == []  # 파일에 대해 startfile을 직접 부르면 안 됨(=실행 금지)
    assert run_calls and "/select," in run_calls[0][0][1]
