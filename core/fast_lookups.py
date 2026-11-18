# -*- coding: utf-8 -*-
"""모델 없이 바로 답하는 읽기 전용 질문 감지 3종 (2026-11-18).

실제 앱에서 아래 질문은 모델이 도구를 고르느라 60~130초가 걸리거나 시간 초과로 멈췄다. 뜻이 하나뿐인 읽기 질문은
정규식으로 알아보고 도구를 코드가 직접 부른다(PC 종합 점검·방해금지 상태 빠른 경로와 같은 원칙).
  1) 타이머 목록   "지금 설정된 타이머 뭐 있어?"            -> list_timers()
  2) 비·우산 예보  "내일 비 와?", "우산 필요해?"            -> get_weather_forecast(when=…)
  3) 최근 받은 파일 "이번주에 받은 파일 찾아줘"              -> search_files(기간, 다운로드 폴더)

이 모듈은 순수 함수만 둔다(Qt·플러그인 import 없음, 부수효과 없음) — 도구 호출·답 만들기는 core/ai_worker.py 가 한다.
공통 원칙: 만들기·지우기·바꾸기 요청, 다른 요청이 섞인 말, 조건 알림("비 오면 알려줘"), 질문이 아닌 잡담은 가로채지 않는다.
말 속에 이 모듈이 모르는 낱말(도시 이름·파일 종류·키워드)이 남아 있으면 모델에 맡긴다 — 모르는 조건을 빼고 답하면 틀린 답이 된다.
"""
import re

_MAX_COMPACT = 36       # 공백을 뺀 글자 수 상한 — 긴 문장은 다른 말이 섞였을 가능성이 크다


def _compact(text) -> str:
    return re.sub(r"[\s!?？.~,，]+", "", str(text or "").lower())


_PARTICLES = ("에서", "에는", "에도", "으로", "이", "가", "은", "는", "도", "에", "를", "을", "만", "요", "야", "해", "돼")


def _understood(compact: str, vocabulary, particles=_PARTICLES) -> bool:
    """문장 전체가 알려진 낱말(+바로 뒤에 붙은 조사·어미)만으로 이루어졌는지.
    한 글자 잔여를 봐주지 않는다 — 조사·어미는 알려진 낱말 바로 뒤에서만 인정하고, 시작이나 모르는 글자 뒤의 조사는 인정하지 않는다.
    그래서 "가야산에 비 와?"의 "가야산"처럼 지명·파일 종류 같은 모르는 말이 하나라도 남으면 False(모델·기존 경로에 맡긴다)."""
    words = "|".join(re.escape(w) for w in sorted(set(vocabulary), key=len, reverse=True))
    ps = "|".join(re.escape(p) for p in sorted(particles, key=len, reverse=True))
    return re.fullmatch(f"(?:(?:{words})(?:{ps})?)+", compact) is not None


# ───────────────────────── 1) 타이머 목록 ─────────────────────────
_TIMER = re.compile(r"타이머")
_TIMER_WRITE = re.compile(
    r"맞춰|맞출|맞추|설정(?:해|하|할|하자)|걸어|걸고|켜(?:줘|주|봐|라|자|기|고)|시작|취소|삭제|지워|끄(?:고|기|자|라)|꺼(?:줘|주|봐)|없애|해제"
    r"|바꿔|바꾸|변경|늘려|줄여|추가|만들|울려|울리|\d+(?:시간|분|초)|(?:한|두|세|네|다섯|여섯|십)(?:시간|분|초)|뒤에|후에")
_TIMER_OTHER = re.compile(r"주고|하고|그리고|랑|도알려|도보여|도확인|일정|날씨|메모|할일|파일|또|및|함께|기능|앱|추천|사용법|설명|방법|어떻게|왜")
_TIMER_ASK = re.compile(r"뭐|무슨|어떤|몇개|몇갠|목록|리스트|남았|남은|있어|있나|있니|있는|있을|설정된|설정한|맞춰져|걸려|돌아|확인|알려|보여|현황|상태|얼마나")


def is_timer_list_question(text) -> bool:
    """"지금 설정된 타이머 뭐 있어?" — 타이머 만들기·취소·시간 지정이 섞이면 가로채지 않는다."""
    c = _compact(text)
    if not c or len(c) > _MAX_COMPACT or not _TIMER.search(c):
        return False
    if _TIMER_WRITE.search(c) or _TIMER_OTHER.search(c):
        return False
    return bool(_TIMER_ASK.search(c))


# list_timers() 원문 형식 — core/ai_worker.py 의 대화체 빌더(_build_timer_list_reply)도 이 정규식을 그대로 쓴다(한 곳에서만 정의)
TIMER_LIST_EMPTY = "[⏱️ 타이머 목록]\n설정된 타이머가 없습니다."
TIMER_LIST_HEADER = re.compile(r"^\[⏱️ 타이머 목록\] \(총 (?P<count>\d+)개\)\n(?P<body>.+)$", re.DOTALL)
TIMER_LIST_ITEM = re.compile(r"^  - (?P<remaining>\d+분 \d+초) 후(?: \('(?P<label>.+)'\))? \(id: (?P<id>\S+)\)$")
TIMER_REPORT_MIN = 6        # 이 개수 이상이면 보고서(적으면 기존 대화체 한 줄 목록)


def timer_report(raw):
    """list_timers() 원문 -> 타이머가 많을 때의 보고서. 개수가 적거나 형식이 다르면 None(기존 대화체 빌더가 이어서 본다)."""
    m = TIMER_LIST_HEADER.match(str(raw or "").strip())
    if not m:
        return None
    declared, items = int(m.group("count")), []
    for ln in m.group("body").split("\n"):
        if not ln.strip():
            continue
        im = TIMER_LIST_ITEM.match(ln)
        if not im:
            return None
        items.append((im.group("label"), im.group("remaining")))
    if len(items) != declared or declared < TIMER_REPORT_MIN:
        return None
    lines = [f"📋 타이머 · {declared}개"]
    lines += [f"{label or f'이름 없는 타이머 {i}'} — {rem} 남음" for i, (label, rem) in enumerate(items, 1)]
    return "\n".join(lines) + "\n\n필요한 타이머는 제가 취소해 드릴 수 있어요."


# ───────────────────────── 2) 비·우산 예보 ─────────────────────────
_RAIN_WORD = re.compile(r"(?<![가-힣])비(?:가|도|는)?\s*(?:안\s*)?(?:와|오|올(?!라)|온|내리|내려|소식|예보)|(?<![가-힣])비[\s?？!.~]*$|우산|강수|폭우|장마|소나기"
                        r"|(?<![가-힣])눈(?:이|도)?\s*(?:와|오|올(?!라)|온|내리|내려)|폭설")
# 조건 알림·과거·이유 질문 같은 다른 의도. 노래·영화·기분 같은 잡담은 아래 _understood 가 "모르는 말"로 걸러내니 여기에 두지 않는다
# ("시"·"책"처럼 짧은 낱말은 "알려주시겠어요"·"책임" 같은 정상 말에 걸려서 뺐다)
_RAIN_NOT = re.compile(r"오면|오거든|오는날|올때|왔|와서|오고|내리면|알림|알람|조건|설정|등록|취소|해제|이유|원인|왜")
_RAIN_NOW = re.compile(r"지금|현재|방금|아까|실시간")
_RAIN_OTHER_DAY = re.compile(r"어제|그제|그저께|지난|다음주|다음달|이번달|글피|언제|며칠|몇일|\d+일|\d+월|[월화수목금토일]요일|저녁|아침|새벽|낮|밤|오전|오후|점심|출근|퇴근|하루종일|내내")
_RAIN_VOCAB = (
    "오늘", "내일모레", "내일", "모레", "이번주말", "이번주", "주말", "일주일", "앞으로", "7일",
    "우산", "비", "눈", "와", "오나", "올까", "올지", "올거", "올것", "올", "오는지", "오니", "온대", "내려", "내릴까", "내리나", "내리니", "내려요",
    "소식", "예보", "강수", "확률", "날씨", "필요한지", "필요할까", "필요할지", "필요한가", "필요", "챙겨야", "챙길까", "챙겨", "가져가야", "가져가", "할까", "해야", "돼", "되나", "될까",
    "있어", "있나", "있니", "있을까", "알려줘", "알려주세요", "알려주시겠어요", "알려주시겠나요", "알려주실래요", "알려줄래요", "알려줄래", "알려줘요",
    "알려주시면", "알려", "줘", "주세요", "보여줘", "보여주세요", "확인해줘", "확인해주세요", "확인", "해줘", "봐줘", "봐주세요", "좀", "혹시", "그럼",
    "많이", "꽤", "엄청", "계속", "하나", "해요", "할지", "할까요", "써야", "쓸까", "들고", "나가야", "나갈", "얼마", "퍼센트", "%", "오는가", "올까",
)


def rain_forecast_when(text):
    """"내일 비 와?"·"우산 필요해?" -> get_weather_forecast 의 when 값(today/tomorrow/day_after_tomorrow/this_weekend/week), 아니면 None.
    도시 이름 같은 모르는 말이 섞이거나 지금 날씨·조건 알림·잡담이면 None(모델·기존 경로에 맡긴다)."""
    raw = str(text or "")
    c = _compact(raw)
    if not c or len(c) > _MAX_COMPACT or not _RAIN_WORD.search(raw):
        return None
    if _RAIN_NOT.search(c) or _RAIN_NOW.search(c) or _RAIN_OTHER_DAY.search(c):
        return None
    if not _understood(c, _RAIN_VOCAB):       # 도시 이름·노래·영화 같은 모르는 말이 섞였다
        return None
    if "모레" in c:
        return "day_after_tomorrow"
    if re.search(r"이번주말|주말", c):
        return "this_weekend"
    if re.search(r"이번주|일주일|앞으로7일", c):
        return "week"
    if "내일" in c:
        return "tomorrow"
    return "today"


# ───────────────────────── 3) 최근 받은 파일 ─────────────────────────
_DL_SUBJECT = re.compile(r"받은(?:파일|거|것)|다운로드(?:한|받은|된)?(?:파일|거|것)|다운(?:로드)?받은(?:파일|거|것)|내려받은(?:파일|거|것)|최근다운로드|다운로드폴더")
# 점검·확인·목록은 받은 파일 점검(보안 단일 점검)이 맡고, 열기·옮기기·지우기·보내기는 이 빠른 경로가 아니다
_DL_NOT = re.compile(r"점검|검사|확인|목록|안전|이상|수상|의심|바이러스|악성|위험|방금|열어|실행|삭제|지워|옮겨|이동|복사|정리|압축|풀어|공유|보내|전송|첨부|바꿔|비교|제일큰|가장큰|큰파일|용량|어디|경로|위치")
_DL_PERIODS = (
    ("오늘", "today", "오늘"), ("어제", "yesterday", "어제"), ("이번주", "this_week", "이번 주"), ("지난주", "last_week", "지난주"),
    ("이번달", "this_month", "이번 달"), ("지난달", "last_month", "지난달"),
)
_DL_DEFAULT_DAYS = 7
_DL_RECENT_SENSE = re.compile(r"받은|다운받은|내려받은|다운로드(?:한|된|받은)|최근|요즘")
_DL_VOCAB = (
    "받은", "파일", "다운로드", "다운받은", "다운로드한", "다운로드받은", "내려받은", "다운로드된", "폴더", "최근", "요즘",
    "오늘", "어제", "이번주", "지난주", "이번달", "지난달", "일주일", "동안", "찾아줘", "찾아", "찾을", "보여줘", "보여", "알려줘", "알려", "줘", "주세요",
    "뭐", "무슨", "어떤", "있어", "있나", "있니", "있는지", "있는", "있을까", "궁금해", "궁금", "좀", "혹시", "내가", "제가",
)


def recent_downloads_request(text):
    """"이번주에 받은 파일 찾아줘" -> (search_files 인자, 기간 낱말) 또는 None.
    파일 종류·이름 단서·폴더 같은 모르는 말이 섞였거나 점검·열기·지우기 말이면 None(모델·기존 경로에 맡긴다).
    기간은 사용자가 말한 것만 쓰고, 없으면 최근 7일(기간을 말하는 안내에 그대로 나온다)."""
    raw = str(text or "")
    c = _compact(raw)
    if not c or len(c) > _MAX_COMPACT or not _DL_SUBJECT.search(c):
        return None
    if _DL_NOT.search(c):
        return None             # "오늘 받은 파일"처럼 요청 낱말 없는 명사구도 받는다 — 모르는 말이 없는지는 아래에서 본다
    days = None
    m = re.search(r"최근(\d{1,3})일", c)
    if m:
        days = int(m.group(1))
        if not 1 <= days <= 365:
            return None
        c = c.replace(m.group(0), "최근")
    if not _understood(c, _DL_VOCAB):      # 파일 종류·이름 단서 같은 모르는 말이 섞였다
        return None
    if days is not None:
        return {"recent_days": days, "time_basis": "created"}, f"최근 {days}일"
    for word, period, label in _DL_PERIODS:
        if word in c:
            return {"period": period, "time_basis": "created"}, label
    if "일주일" in c:
        return {"recent_days": 7, "time_basis": "created"}, "최근 7일"
    # 기간 낱말이 없을 때의 7일 기본값은 "받은 파일"·"최근"처럼 '받은 것'을 묻는 말일 때만이다.
    # "다운로드 폴더에 뭐 있어?"는 폴더 내용을 묻는 것이라 7일로 거르면 틀린 답이 된다 -> 모델·기존 경로에 맡긴다
    if not _DL_RECENT_SENSE.search(c):
        return None
    return {"recent_days": _DL_DEFAULT_DAYS, "time_basis": "created"}, f"최근 {_DL_DEFAULT_DAYS}일"


_NO_MATCH = re.compile(r"^\[🔎 파일 검색 결과\].*\n조건에 맞는 파일을 찾지 못했어요\. \(파일 (?P<n>\d+)개 확인\)(?P<rest>.*)$", re.DOTALL)


def no_files_reply(raw, label: str):
    """검색 결과 없음 원문 -> 한 줄 대화체. 결과 없음 형식이 아니면 None."""
    m = _NO_MATCH.match(str(raw or "").strip())
    if not m:
        return None
    note = " 파일이 많아 일부만 확인했어요." if "상한" in m.group("rest") else ""
    return f"{label}에 받은 파일은 찾지 못했어요. (다운로드 폴더에서 파일 {m.group('n')}개를 확인했어요.){note}"
