# -*- coding: utf-8 -*-
"""
core/ai_worker.py의 AIWorker._resolve_folder_path() 회귀 테스트.

실사용 재현 버그(2026-09-29): "바탕화면의 Team-BuildIt 폴더에서 100mb 이상의
파일을 찾아줘"에서 folder/directory 인자가 조용히 사라지고 기본 스캔 폴더
(다운로드 등)로 새는 걸 확인했다 — LLM은 "바탕화면의 Team-BuildIt"을
"C:\\Users\\...\\Desktop\\Team-BuildIt"로 올바르게 해석했지만, 이 절대경로
문자열 자체는 사용자의 한국어 문장에 그대로 없어서 기존 방어("LLM 값이 사용자
문장의 리터럴 substring일 때만 인정" — LLM이 지어낸 엉뚱한 경로를 막기 위한
장치)가 정상적인 경로까지 전부 걸러냈다.

Path.home()을 임시 폴더로 바꿔치기해서 실제 사용자의 바탕화면/다운로드 폴더를
건드리지 않고 검증한다.
"""
import pathlib

import pytest

from core.ai_worker import AIWorker


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """Path.home()을 임시 폴더로 바꾸고, Desktop/Downloads/Documents와 그 안에
    하위 폴더 하나(Team-BuildIt)를 실제로 만들어둔다."""
    desktop = tmp_path / "Desktop"
    downloads = tmp_path / "Downloads"
    documents = tmp_path / "Documents"
    for d in (desktop, downloads, documents):
        d.mkdir()
    (desktop / "Team-BuildIt").mkdir()
    (desktop / "다른폴더").mkdir()
    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


def _resolve(text, llm_value=""):
    return AIWorker(text, [], [])._resolve_folder_path(text, llm_value)


# ── 실제 재현된 버그 시나리오 ────────────────────────────────────────────

def test_desktop_subfolder_resolves_to_absolute_path(fake_home):
    """가장 중요한 회귀 테스트 — 실제로 재현된 버그."""
    result = _resolve(
        "바탕화면의 Team-BuildIt 폴더에서 100mb 이상의 파일을 찾아줘",
        llm_value="C:/Users/someone/Desktop/Team-BuildIt",
    )
    assert result == str(fake_home / "Desktop" / "Team-BuildIt")


def test_desktop_subfolder_resolves_even_without_llm_value(fake_home):
    """LLM이 아예 folder/directory 인자를 안 줬어도(생략) 사용자 문장만으로
    해석할 수 있어야 한다."""
    result = _resolve("바탕화면의 Team-BuildIt 폴더에서 큰 파일 찾아줘", llm_value="")
    assert result == str(fake_home / "Desktop" / "Team-BuildIt")


@pytest.mark.parametrize("phrase", [
    "바탕화면의 Team-BuildIt 폴더에서 찾아줘",
    "바탕화면에 있는 Team-BuildIt 폴더에서 찾아줘",
    "바탕화면 Team-BuildIt 폴더에서 찾아줘",
])
def test_various_connector_phrasings_resolve_subfolder(fake_home, phrase):
    result = _resolve(phrase, llm_value="Desktop/Team-BuildIt")
    assert result == str(fake_home / "Desktop" / "Team-BuildIt")


# ── 기준 폴더만 있고 하위 폴더 이름이 없는 경우 ───────────────────────────

def test_bare_known_folder_without_subfolder_resolves_to_base(fake_home):
    result = _resolve("다운로드 폴더에서 큰 파일 찾아줘")
    assert result == str(fake_home / "Downloads")


def test_bare_desktop_mention_without_folder_word(fake_home):
    result = _resolve("바탕화면에서 100mb 넘는 파일 찾아줘")
    assert result == str(fake_home / "Desktop")


# ── 존재하지 않는 하위 폴더/기준 폴더 자체 없음 ───────────────────────────

def test_nonexistent_subfolder_falls_back_to_base_folder(fake_home):
    """LLM이 제안한 하위 폴더 이름이 실제로는 존재하지 않으면(오타 등),
    최소한 기준 폴더까지는 안전하게 폴백한다."""
    result = _resolve(
        "바탕화면의 존재하지않는폴더에서 찾아줘",
        llm_value="Desktop/존재하지않는폴더",
    )
    assert result == str(fake_home / "Desktop")


def test_llm_value_tail_not_in_text_is_ignored(fake_home):
    """LLM이 지어낸 하위 폴더 이름(사용자 문장에 없는)은 무시하고 기준
    폴더만 인정한다 — 기존 "LLM이 지어낸 경로 차단" 원칙 유지."""
    result = _resolve(
        "바탕화면에서 찾아줘",
        llm_value="Desktop/다른폴더",  # 사용자 문장에 없는 이름을 LLM이 지어낸 상황
    )
    assert result == str(fake_home / "Desktop")


# ── path traversal 방어(ChatGPT 검수 지적, 2026-09-30) ───────────────────

def test_dotdot_subfolder_does_not_escape_base_via_regex_path(fake_home):
    """정규식 추출 경로(LLM 값 없이 사용자 문장만으로 하위 폴더명을 뽑는
    분기)에서 sub_name이 ".."가 되면 base(바탕화면) 밖으로 벗어날 수 있었던
    실제 버그의 회귀 테스트 — base 내부로 안전하게 폴백해야 한다."""
    result = _resolve("바탕화면의 .. 폴더에서 찾아줘")
    assert result == str(fake_home / "Desktop")


def test_dotdot_subfolder_does_not_escape_base_via_llm_value(fake_home):
    """LLM 값 경로(tail)로 sub_name이 결정되는 분기에서도 동일하게 방어돼야
    한다 — llm_value의 마지막 조각이 ".."이고 사용자 문장에도 ".."이 그대로
    있는(드물지만 가능한) 경우."""
    result = _resolve("바탕화면의 .. 에서 찾아줘", llm_value="Desktop/..")
    assert result == str(fake_home / "Desktop")


def test_nested_dotdot_does_not_escape_base(fake_home):
    """Team-BuildIt/../../ 처럼 하위 폴더 이름 자체에 상위 이동이 섞여 있어도
    (regex가 "폴더" 직전까지 통째로 캡처하므로) base 밖으로 못 나가야 한다."""
    (fake_home / "Desktop" / "Team-BuildIt").mkdir(exist_ok=True)
    result = _resolve("바탕화면의 Team-BuildIt/../../ 폴더에서 찾아줘")
    assert result == str(fake_home / "Desktop")


def test_no_known_folder_keyword_returns_none(fake_home):
    """기준 폴더 키워드가 아예 없으면 None — 호출부가 기존 리터럴 substring
    방식으로 폴백해야 한다."""
    assert _resolve("아무 폴더나 찾아줘", llm_value="C:/some/made/up/path") is None


def test_empty_text_returns_none(fake_home):
    assert _resolve("", llm_value="") is None


# ── search_files/find_large_files 디스패치 통합 확인 ─────────────────────

def test_search_files_dispatch_resolves_desktop_subfolder(fake_home, qapp, monkeypatch):
    """실제 run() 디스패치 경로에서 folder 인자가 살아남는지 확인 —
    가짜 ollama로 LLM이 엉뚱한 절대경로를 줘도 코드가 직접 계산한 경로로
    교정되는지가 핵심."""
    import core.ai_worker as ai_worker

    def search_files(keyword="", file_type="", period="", time_basis="", folder="",
                     min_size_mb=0, max_size_mb=0, recent_days=0):
        calls.append(folder)
        return "가짜 검색 결과"

    calls = []
    state = {"n": 0}

    def fake_chat(*a, **kw):
        state["n"] += 1
        if state["n"] == 1:
            return {"message": {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "search_files",
                              "arguments": {"folder": "C:/Users/other/Desktop/Team-BuildIt"}}}]}}
        return {"message": {"role": "assistant", "content": "완료"}}

    monkeypatch.setattr(ai_worker.ollama, "chat", fake_chat)
    worker = AIWorker("바탕화면의 Team-BuildIt 폴더에서 pdf 찾아줘", [], [search_files])
    worker.run()
    assert calls == [str(fake_home / "Desktop" / "Team-BuildIt")]


def test_find_large_files_dispatch_resolves_desktop_subfolder(fake_home, qapp, monkeypatch):
    import core.ai_worker as ai_worker

    def find_large_files(directory="", min_size_mb=100, top_n=10):
        calls.append(directory)
        return "가짜 대용량 결과"

    calls = []
    state = {"n": 0}

    def fake_chat(*a, **kw):
        state["n"] += 1
        if state["n"] == 1:
            return {"message": {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "find_large_files",
                              "arguments": {"directory": "C:/Users/other/Desktop/Team-BuildIt",
                                            "min_size_mb": 999}}}]}}
        return {"message": {"role": "assistant", "content": "완료"}}

    monkeypatch.setattr(ai_worker.ollama, "chat", fake_chat)
    worker = AIWorker("바탕화면의 Team-BuildIt 폴더에서 100mb 이상의 파일을 찾아줘", [], [find_large_files])
    worker.run()
    assert calls == [str(fake_home / "Desktop" / "Team-BuildIt")]


def test_find_duplicate_files_dispatch_resolves_desktop_subfolder(fake_home, qapp, monkeypatch):
    """2026-09-29 실사용 감사에서 발견: find_duplicate_files도 directory
    인자를 받는데 search_files/find_large_files와 달리 여태 아무 해석/검증도
    안 거쳤다 — 같은 헬퍼로 통일했는지 확인."""
    import core.ai_worker as ai_worker

    def find_duplicate_files(directory=""):
        calls.append(directory)
        return "가짜 중복 파일 결과"

    calls = []
    state = {"n": 0}

    def fake_chat(*a, **kw):
        state["n"] += 1
        if state["n"] == 1:
            return {"message": {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "find_duplicate_files",
                              "arguments": {"directory": "C:/Users/other/Desktop/Team-BuildIt"}}}]}}
        return {"message": {"role": "assistant", "content": "완료"}}

    monkeypatch.setattr(ai_worker.ollama, "chat", fake_chat)
    worker = AIWorker("바탕화면의 Team-BuildIt 폴더에서 중복 파일 찾아줘", [], [find_duplicate_files])
    worker.run()
    assert calls == [str(fake_home / "Desktop" / "Team-BuildIt")]


def test_find_duplicate_files_drops_hallucinated_directory_when_unresolvable(fake_home, qapp, monkeypatch):
    """기준 폴더 키워드가 전혀 없고 LLM 값도 사용자 문장에 없으면(지어낸
    경로) 기존 방어 원칙대로 버려야 한다 — 이전엔 이 검증 자체가 없어서
    LLM이 지어낸 경로를 그냥 믿는 상태였다."""
    import core.ai_worker as ai_worker

    def find_duplicate_files(directory=""):
        calls.append(directory)
        return "가짜 중복 파일 결과"

    calls = []
    state = {"n": 0}

    def fake_chat(*a, **kw):
        state["n"] += 1
        if state["n"] == 1:
            return {"message": {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "find_duplicate_files",
                              "arguments": {"directory": "C:/completely/made/up/path"}}}]}}
        return {"message": {"role": "assistant", "content": "완료"}}

    monkeypatch.setattr(ai_worker.ollama, "chat", fake_chat)
    worker = AIWorker("중복 파일 찾아줘", [], [find_duplicate_files])
    worker.run()
    assert calls == [""]  # 지어낸 경로가 아니라 기본값(빈 문자열=기본 폴더들)이 전달돼야 함
