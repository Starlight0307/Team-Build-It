"""
2026-10-13 실제 앱 점검에서 "답변이 자꾸 오류가 난다"는 신고로 찾은 문제들의 회귀 테스트.
(실제 llama3.1은 부르지 않는다 — 판단 함수와 결정론적 처리만 확인)

- 20자 이하 짧은 말은 직전 AI 답변까지 키워드를 훑는데, "고마워"/"좋아" 같은 맞장구도 그렇게 해서
  도구 요청으로 분류됐다 → 모델이 말로 답하면 "방금 요청을 정확하게 확인/처리하지 못했어요"로 바뀜.
- "F1이 뭔지 설명해줘"가 '기능 사용법 설명' 모드로 가서 없는 기능을 지어냈다.
- 캘린더 "연결이 필요해요" 안내를 LLM이 이상하게 고쳐 썼다.
- 번역 도구 인자를 LLM이 망가뜨렸다('입해의'), 뉴스·환율은 도구가 없어 엉뚱한 도구를 불렀다.
"""
import pytest

from core.ai_worker import AIWorker, _build_deterministic_reply


def _worker(text, history=None, tools=None):
    return AIWorker(user_text=text, chat_history=history or [], installed_tools=tools or [])


WEATHER_REPLY = [{'role': 'user', 'content': '오늘 서울 날씨 어때?'},
                 {'role': 'assistant', 'content': '☀️ 지금 서울은(는) 맑음, 20°C예요. 날씨 정보를 확인했어요.'}]


@pytest.mark.parametrize("text", ["좋아", "응", "고마워", "고마워요", "알겠어", "ㅇㅋ", "네!"])
def test_ack_after_plain_answer_does_not_borrow_previous_reply(qapp, text):
    w = _worker(text, list(WEATHER_REPLY))
    assert w._keyword_search_text() == text.lower()


def test_ack_after_offer_still_uses_previous_reply(qapp):
    """'응'이 '막아드릴까요?' 제안에 대한 승낙이면 기존처럼 직전 답을 맥락으로 쓴다."""
    history = [{'role': 'assistant', 'content': '포트 445가 열려 있어서 위험할 수 있어요. 막아드릴까요?'}]
    w = _worker("응", history)
    assert '포트 445' in w._keyword_search_text()


def test_short_question_without_tool_words_is_not_a_tool_request_by_itself(qapp):
    w = _worker("100 곱하기 37은?", list(WEATHER_REPLY))
    assert not w._user_text_needs_tools()


def test_general_explain_is_not_feature_explanation(qapp):
    assert not _worker("F1이 뭔지 설명해줘")._is_explanation_request()
    assert not _worker("양자역학 설명해줘")._is_explanation_request()


def test_feature_explanations_still_detected(qapp):
    assert _worker("루미 타이머 기능 설명해줘")._is_explanation_request()
    assert _worker("일정 추가하는 방법 알려줘")._is_explanation_request()
    assert _worker("캘린더 사용법 알려줘")._is_explanation_request()


def test_calendar_connection_needed_passes_through_unchanged():
    raw = ('[🔔 내일 일정 브리핑] 2026-10-14\n현재 시각: 15:31\n─────────────────────\n'
           '애플 캘린더를 쓰려면 연결이 필요해요.\n마이페이지의 [애플 연동하기]에서 연결해 주세요.')
    assert _build_deterministic_reply(raw) == (
        "애플 캘린더를 쓰려면 연결이 필요해요. 마이페이지의 [애플 연동하기]에서 연결해 주세요.")


def _run_with_tools(qapp, text, tools):
    out = []
    w = _worker(text, [], tools)
    w.response_ready.connect(out.append)
    w.run()
    return out


def test_translation_request_calls_translate_text_with_parsed_args(qapp):
    calls = []

    def translate_text(text, target_language):
        calls.append((text, target_language))
        return "[📄 번역 결과 (영어)]\nHello!"

    out = _run_with_tools(qapp, "영어로 '안녕하세요'를 번역해줘", [translate_text])
    assert calls == [("안녕하세요", "영어")]
    assert out and "Hello!" in out[0]

    calls.clear()
    _run_with_tools(qapp, "'감사합니다'를 일본어로 번역해줘", [translate_text])
    assert calls == [("감사합니다", "일본어")]


@pytest.mark.parametrize("text,word", [("오늘 뉴스 알려줘", "뉴스"), ("환율 알려줘", "환율")])
def test_news_and_exchange_without_plugin_answer_honestly(qapp, text, word):
    out = _run_with_tools(qapp, text, [])
    assert out and f"아직 {word} 정보를 가져오는 기능은 없어요" in out[0]


def test_news_site_open_request_is_not_blocked(qapp):
    """'뉴스 사이트 열어줘'는 정보 요청이 아니라 안내 문구로 막지 않는다."""
    w = _worker("네이버 뉴스 사이트 열어줘")
    # 빠른 감지 조건과 같은 판단: 정보 요청 단어가 없음
    assert not any(k in w.user_text for k in ('알려', '보여', '어때', '얼마', '뭐야', '있어', '정리', '브리핑'))


@pytest.mark.parametrize("text,expect", [("좋아", "좋아요!"), ("응", "좋아요!"), ("고마워", "천만에요!")])
def test_plain_ack_gets_fixed_friendly_reply(qapp, text, expect):
    out = []
    w = _worker(text, list(WEATHER_REPLY))
    w.response_ready.connect(out.append)
    w.run()
    assert out and expect in out[0]


def test_ack_to_offer_is_not_answered_with_fixed_reply(qapp):
    """'~할까요?'에 대한 '응'은 맞장구가 아니라 승낙 — 고정 대답으로 가로채면 안 된다."""
    history = [{'role': 'assistant', 'content': '임시 파일을 정리해드릴까요?'}]
    w = _worker("응", history)
    prev = history[-1]['content']
    assert w._ACK_ONLY.match("응") and ('?' in prev or '까요' in prev)


def test_price_no_result_message_is_shown_as_is(qapp):
    def search_product_price(query="", keyword=""):
        return f"'{query}'에 대한 검색 결과를 가져오지 못했어요. 상품 이름을 조금 바꾸거나 잠시 뒤 다시 검색해 주세요."

    out = _run_with_tools(qapp, "아이폰 16 최저가 검색해줘", [search_product_price])
    assert out and "가져오지 못했어요" in out[0]
    assert "이미 계산됨" not in out[0]
