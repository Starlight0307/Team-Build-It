# -*- coding: utf-8 -*-
"""
날씨 예보(get_weather_forecast) 답변을 사람 말로 바꾸는 순수 모듈 (2026-11-12).

도구 원문 `  - 2026-11-13: ☁️ 흐림, 7~17°C, 강수확률 5%` 한 줄만 보여 주던 것을
  - 하루·이틀 예보: 결론 먼저, 대화체 한두 문장
  - 사흘 이상(이번주 날씨 등): 보고서(`📋 날씨 예보 · N일`) + 마지막 한 줄 대화체
로 바꾼다. 없는 숫자는 지어내지 않는다(원문에서 읽은 값만 쓴다). Qt·플러그인 import 없음.
"""
import re
from datetime import date

# 강수확률 기준(%) — 이 값 이상이면 해당 문구
RAIN_HIGH = 50      # 이상: 비가 올 가능성이 높아요
RAIN_MID = 30       # 이상(HIGH 미만): 비가 올 수도 있어요 / 미만: 가능성은 낮아요
REPORT_MIN_DAYS = 3  # 이 일수 이상이면 보고서

# 헤더·줄 정규식은 문자 클래스+길이 상한으로 선형 시간(겹치는 .+?가 만드는 폭발을 막는다)
_HEADER = re.compile(r"^\[🌦️ (?P<city>[^\]\n ]{1,40}) (?P<label>[^\]\n]{1,30}?) 날씨 예보\]$")
_ROW = re.compile(r"^\s*-\s*(?P<date>\d{4}-\d{2}-\d{2}):\s*(?P<icon>\S{1,8}) (?P<desc>[^,\n]{1,40}), ?"
                  r"(?P<tmin>-?\d{1,3})~(?P<tmax>-?\d{1,3})°C, ?강수확률 ?(?P<prob>\d{1,3})% ?$")
# 질문 판별 — 앞에 한글이 붙은 말("대비/준비/비올라/한눈")은 제외하고, "비 안 와/비 온대/비 소식/내일 비(끝)"는 잡는다
_VERB = r"(?:이|가)?\s*(?:안\s*)?(?:와|오|올(?!라)|온|내리|내려|소식|예보)"
_RAIN_ONLY = re.compile(r"(?<![가-힣])비" + _VERB + r"|(?<![가-힣])비[\s?？!.~]*$|우산|강수|폭우|장마|소나기")
_SNOW_ONLY = re.compile(r"(?<![가-힣])눈" + _VERB + r"|(?<![가-힣])눈[\s?？!.~]*$|폭설")
_WEEKDAYS = "월화수목금토일"
FAIL_TEXT = "예보를 가져오지 못했어요."

# 날씨 설명 → 문장 중간에 쓰는 말
_DESC_FORM = {
    "맑음": "맑고", "대체로 맑음": "대체로 맑고", "구름 조금": "구름이 조금 끼고", "흐림": "흐리고",
    "안개": "안개가 끼고", "짙은 안개": "짙은 안개가 끼고",
    "약한 이슬비": "약한 이슬비가 내리고", "이슬비": "이슬비가 내리고", "강한 이슬비": "강한 이슬비가 내리고",
    "약한 비": "약한 비가 내리고", "비": "비가 내리고", "강한 비": "강한 비가 내리고",
    "어는 비": "어는 비가 내리고", "강한 어는 비": "강한 어는 비가 내리고",
    "약한 눈": "약한 눈이 내리고", "눈": "눈이 내리고", "많은 눈": "눈이 많이 내리고", "싸락눈": "싸락눈이 내리고",
    "소나기": "소나기가 내리고", "강한 소나기": "강한 소나기가 내리고", "눈보라": "눈보라가 치고",
    "강한 눈보라": "강한 눈보라가 치고", "뇌우": "천둥번개가 치고", "우박 뇌우": "우박과 천둥번개가 치고",
    "강한 우박 뇌우": "우박과 천둥번개가 강하게 치고",
}


def _is_snow(desc: str) -> bool:
    return "눈" in desc or "싸락" in desc


def parse(raw):
    """원문 → (label, [dict(date, icon, desc, tmin, tmax, prob)]) 또는 None(예보 원문이 아님)."""
    lines = [ln for ln in str(raw or "").strip().splitlines() if ln.strip()]
    if not lines:
        return None
    m = _HEADER.match(lines[0].strip())
    if not m:
        return None
    days = []
    for ln in lines[1:]:
        r = _ROW.match(ln)
        if not r:
            return None            # 예상 밖 줄이 섞이면 건드리지 않는다
        days.append({"date": r.group("date"), "icon": r.group("icon"), "desc": r.group("desc").strip(),
                     "tmin": int(r.group("tmin")), "tmax": int(r.group("tmax")), "prob": int(r.group("prob"))})
    return m.group("label"), days


def date_label(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.month}월 {d.day}일 {_WEEKDAYS[d.weekday()]}요일"


def topic(label: str) -> str:
    """라벨 + 은/는(받침 규칙): 내일은·모레는·오늘은·11월 13일 금요일은·이번 주말은."""
    last = (label or " ")[-1]
    if "가" <= last <= "힣" and (ord(last) - 0xAC00) % 28 == 0:
        return f"{label}는"
    return f"{label}은"


def _ye(word: str) -> str:
    last = word[-1]
    return word + ("예요" if "가" <= last <= "힣" and (ord(last) - 0xAC00) % 28 == 0 else "이에요")


def _deg(n: int) -> str:
    return f"영하 {-n}도" if n < 0 else f"{n}도"


def _temp_range(d) -> str:
    """'7도에서 17도' / '영하 3도에서 5도' / 같으면 '5도'."""
    return _deg(d["tmin"]) if d["tmin"] == d["tmax"] else f"{_deg(d['tmin'])}에서 {_deg(d['tmax'])}"


def _temp_sentence(d) -> str:
    if d["tmin"] == d["tmax"]:
        return f"기온은 {_deg(d['tmin'])}예요."
    return f"기온은 {_temp_range(d)} 사이예요."


def _is_precip(desc: str) -> bool:
    return any(k in desc for k in ("비", "눈", "이슬", "소나기", "뇌우", "싸락"))


_CLEAR = ("맑음", "대체로 맑음", "구름 조금")


def rain_phrase(prob: int, snow: bool = False) -> str:
    noun = "눈이" if snow else "비가"
    if prob >= RAIN_HIGH:
        return f"{noun} 올 가능성이 높아요"
    if prob >= RAIN_MID:
        return f"{noun} 올 수도 있어요"
    return ("눈" if snow else "비") + " 올 가능성은 낮아요"


def question_kind(question: str):
    """질문 → (비·눈 질문인지, 눈 질문인지). 눈 말만 있으면 눈 기준, 둘 다 있거나 비면 비/설명 기준."""
    q = question or ""
    rain, snow = bool(_RAIN_ONLY.search(q)), bool(_SNOW_ONLY.search(q))
    return rain or snow, snow and not rain


def _day_sentences(d, label: str, rain_question: bool, snow_question: bool = False) -> str:
    desc, prob = d["desc"], d["prob"]
    known = desc in _DESC_FORM
    form = _DESC_FORM.get(desc)
    precip = _is_precip(desc)
    snow_desc = _is_snow(desc)
    lab = topic(label)
    tail = f"(강수확률 {prob}퍼센트)"
    umbrella = " 우산이 있으면 좋겠어요." if prob >= RAIN_HIGH and not snow_desc and not snow_question else ""
    if snow_question and not snow_desc:       # 눈 질문인데 눈 예보가 아님 — 거짓 없이
        info = f"(예보는 {desc}, 강수확률 {prob}퍼센트)" if known else tail
        return f"{label} 눈 소식은 없어요{info}. {_temp_sentence(d)}"
    if rain_question:
        noun_snow = snow_desc or snow_question
        rp = rain_phrase(prob, noun_snow)
        if precip:                              # 설명이 비·눈이면 같은 말을 겹치지 않는다
            rp = rp.replace("올 ", "내릴 ", 1)
            info = f"강수확률 {prob}퍼센트" if desc in ("비", "눈") else f"{desc}, 강수확률 {prob}퍼센트"
            return f"{lab} {rp}({info}). {_temp_sentence(d)}{umbrella}"
        if known and desc in _CLEAR and prob >= RAIN_MID:
            noun = "눈이" if noun_snow else "비가"
            return (f"{lab} {form[:-1]}지만 강수확률이 {prob}퍼센트라 {noun} 올 수도 있어요. "
                    f"{_temp_sentence(d)}{umbrella}")
        lead = f"{lab} {form} {rp}" if known else f"{lab} {rp}"
        return f"{lead}{tail}. {_temp_sentence(d)}{umbrella}"
    # 날씨 질문
    if precip and known:
        return f"{label} 날씨는 {_ye(desc)}. {_temp_sentence(d)} 강수확률은 {prob}퍼센트예요.{umbrella}"
    rp = rain_phrase(prob, False)
    temp = _temp_sentence(d)
    if known:
        lead = f"{lab} {form[:-1]}지만" if (desc in _CLEAR and prob >= RAIN_MID) else f"{lab} {form}"
        return f"{lead}, {temp} 강수확률은 {prob}퍼센트로 {rp}.{umbrella}"
    return f"{label} {temp} 강수확률은 {prob}퍼센트로 {rp}.{umbrella}"


def _conclusion(days, snow_question: bool) -> str:
    if snow_question:
        cands = [x for x in days if _is_snow(x["desc"])]
        if not cands:
            return f"이 기간에는 눈 소식이 없어요(강수확률은 가장 높은 날이 {max(x['prob'] for x in days)}퍼센트)."
        top = max(cands, key=lambda x: x["prob"])
        return f"이 기간에는 {date_label(top['date'])}에 눈이 올 가능성이 가장 높아요(강수확률 {top['prob']}퍼센트)."
    top = max(days, key=lambda x: x["prob"])
    if top["prob"] >= RAIN_MID:
        snow = _is_snow(top["desc"])
        return (f"이 기간에는 {date_label(top['date'])}에 {'눈' if snow else '비'}이 올 가능성이 가장 높아요"
                f"(강수확률 {top['prob']}퍼센트)." if snow else
                f"이 기간에는 {date_label(top['date'])}에 비가 올 가능성이 가장 높아요(강수확률 {top['prob']}퍼센트).")
    return f"이 기간에는 비 올 가능성이 낮아요(가장 높은 날도 강수확률 {top['prob']}퍼센트)."


def _report(days, rain_question: bool = False, snow_question: bool = False) -> str:
    lines = [f"📋 날씨 예보 · {len(days)}일"]
    for d in days:
        lines.append(f"{d['icon']} {date_label(d['date'])} — {d['desc']}, {_temp_range(d)}"
                     f"(강수확률 {d['prob']}퍼센트)")
    body = "\n".join(lines)
    if rain_question:       # 결론 먼저: 맨 위 한 줄, 맺음말은 겹치지 않는 닫는 말
        return f"{_conclusion(days, snow_question)}\n\n{body}\n\n👉 날짜별 예보는 위에 정리해 두었어요."
    top = max(days, key=lambda x: x["prob"])
    if top["prob"] >= RAIN_MID:
        snow = _is_snow(top["desc"])
        close = (f"👉 {'눈이' if snow else '비가'} 올 가능성이 가장 높은 날은 "
                 f"{date_label(top['date'])}(강수확률 {top['prob']}퍼센트)이에요.")
    else:
        close = "👉 이 기간에는 비 올 가능성이 낮아 보여요."
    return f"{body}\n\n{close}"


def build(raw, question: str = ""):
    """예보 원문 → 사람 말 답. 예보 원문이 아니면 None(다른 빌더가 이어서 본다)."""
    text = str(raw or "").strip()
    if text.startswith("⚠️") and "예보" in text:      # 도구 실패 — 지어내지 않고 그대로 알린다
        inner = text.lstrip("⚠️ ").strip()
        return inner if "가져오지 못했어요" in inner else f"{FAIL_TEXT} ({inner})"
    parsed = parse(text)
    if parsed is None:
        return None
    label, days = parsed
    if not days:
        return FAIL_TEXT
    rain_q, snow_q = question_kind(question)
    if len(days) >= REPORT_MIN_DAYS:
        return _report(days, rain_q, snow_q)
    out = []
    for d in days:
        lab = label if (len(days) == 1 and label in ("오늘", "내일", "모레")) else date_label(d["date"])
        out.append(_day_sentences(d, lab, rain_q, snow_q))
    return " ".join(out)
