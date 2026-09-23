# -*- coding: utf-8 -*-
"""
Agent 평가셋 — "Argument accuracy" 케이스 목록 (③ 평가 하네스 2단계).

tool_selection_cases.py(v1)는 "llama3.1이 올바른 도구를 고르는가"만 쟀다.
이 파일은 그 다음 질문 — "올바른 도구를 골랐을 때, 그 도구에 넣는 인자
(argument)까지 올바르게 채우는가?" — 를 잰다. 도구 선택은 맞아도 인자가
틀리면(예: "10분 뒤에 알려줘"인데 minutes=1을 넣음) 사용자 입장에서는
여전히 고장난 기능이다.

범위(의도적으로 좁힘 — tool_selection_cases.py와 같은 원칙):
- _DANGEROUS_FUNCS(kill_process 등)는 제외한다. 확인창 문구만 나가고 실제
  함수가 호출되지 않아서 인자를 직접 비교할 대상 자체가 없다.
- control_iot_device처럼 "실제 등록된 기기 이름과 정확히 일치해야" 하는
  함수는 제외한다. 테스트 환경에 실제 Kasa 기기가 없으면 discover_iot_devices로
  대체 호출되거나 device_name을 임의로 지어낼 수밖에 없어서, 인자 정확도가
  모델 능력이 아니라 테스트 환경(기기 유무)에 좌우된다 — tool_selection_cases.py가
  이 함수를 "any_of"로 완화해둔 것과 같은 이유.
- 날짜/시각 표현 중 자연어로 애매한 것(예: "저녁 10시"가 22시인지 10시인지)은
  피하거나 OneOf로 양쪽 다 정답 처리한다 — 이건 "인자를 파라미터 스키마에
  맞게 채우는가"를 재는 게 목적이지, 모델의 자연어 시간 해석 능력 자체를
  채점하는 게 목적이 아니다.
- 함수가 여러 인자를 받아도, 사용자 발화에서 값을 명확히 특정할 수 있는
  인자만 검증한다(예: search_files의 folder는 발화에 없으면 검증 안 함).
  "검증 안 함"은 "아무 값이나 정답"이 아니라 "이번 v1에서는 이 인자를
  아예 채점하지 않는다"는 뜻 — expected_args에 없는 키는 실제로 뭐가
  들어왔는지 채점 루프가 쳐다보지도 않는다.

매처(matcher) — core/ 등 다른 실행 경로에는 없고 이 평가셋 전용:
- Exact(v): 정확히 같아야 정답 (주로 숫자/enum)
- OneOf(*values): 여러 값 중 하나면 정답 (자연어 해석이 여러 갈래로 갈릴 수 있는 경우)
- Contains(*substrings): 문자열에 전부(공백/대소문자 무시) 포함돼야 정답 (자유 텍스트)
"""


class Exact:
    def __init__(self, value):
        self.value = value

    def matches(self, actual) -> bool:
        return actual == self.value

    def __repr__(self):
        return f"Exact({self.value!r})"


class OneOf:
    def __init__(self, *values):
        self.values = values

    def matches(self, actual) -> bool:
        return actual in self.values

    def __repr__(self):
        return f"OneOf{self.values!r}"


class Contains:
    def __init__(self, *substrings):
        self.substrings = substrings

    def matches(self, actual) -> bool:
        if not isinstance(actual, str):
            return False
        normalized = actual.replace(" ", "").lower()
        return all(s.replace(" ", "").lower() in normalized for s in self.substrings)

    def __repr__(self):
        return f"Contains{self.substrings!r}"


ARGUMENT_CASES = [
    # ── price_search ──
    {
        "id": "arg_price_1", "text": "아이폰 15 최저가 알려줘",
        "expected_func": "search_product_price",
        "expected_args": {"query": Contains("아이폰", "15")},
    },
    {
        "id": "arg_price_2", "text": "맥북 프로 가격 검색해줘",
        "expected_func": "search_product_price",
        "expected_args": {"query": Contains("맥북")},
    },

    # ── reminder: set_timer ──
    {
        "id": "arg_timer_1", "text": "10분 뒤에 알려줘",
        "expected_func": "set_timer",
        "expected_args": {"minutes": Exact(10)},
    },
    {
        "id": "arg_timer_2", "text": "30분 뒤에 스트레칭 하라고 알려줘",
        "expected_func": "set_timer",
        "expected_args": {"minutes": Exact(30), "label": Contains("스트레칭")},
    },

    # ── reminder: set_daily_reminder (모호하지 않은 오전 시각만 사용) ──
    {
        "id": "arg_daily_1", "text": "매일 아침 9시에 알림 설정해줘",
        "expected_func": "set_daily_reminder",
        "expected_args": {"hour": Exact(9)},
    },
    {
        "id": "arg_daily_2", "text": "매일 오전 7시에 물 마시라고 알려줘",
        "expected_func": "set_daily_reminder",
        "expected_args": {"hour": Exact(7), "label": Contains("물")},
    },

    # ── expense_tracker: set_monthly_budget ──
    {
        "id": "arg_budget_1", "text": "이번달 예산 50만원으로 잡아줘",
        "expected_func": "set_monthly_budget",
        "expected_args": {"amount": Exact(500000)},
    },
    {
        "id": "arg_budget_2", "text": "한달에 30만원까지만 쓰고 싶어",
        "expected_func": "set_monthly_budget",
        "expected_args": {"amount": Exact(300000)},
    },

    # ── reminder: 조건부 알림 ──
    {
        "id": "arg_condition_1", "text": "게임 하루 4시간 넘으면 알려줘",
        "expected_func": "set_usage_condition",
        "expected_args": {"target": Contains("게임"), "threshold_minutes": Exact(240)},
    },
    {
        "id": "arg_condition_2", "text": "이번달 지출 50만원 넘으면 알려줘",
        "expected_func": "set_spending_condition",
        "expected_args": {"threshold_amount": Exact(500000)},
    },

    # ── file_search ──
    {
        "id": "arg_file_1", "text": "pdf 파일 찾아줘",
        "expected_func": "search_files",
        "expected_args": {"file_type": OneOf("pdf", ".pdf", "PDF")},
    },
    {
        "id": "arg_file_2", "text": "지난주에 받은 파일 찾아줘",
        "expected_func": "search_files",
        # file_type은 발화에 없으므로 검증 안 함 — period/time_basis만 검증.
        "expected_args": {"period": Exact("last_week"), "time_basis": Exact("created")},
    },
]
