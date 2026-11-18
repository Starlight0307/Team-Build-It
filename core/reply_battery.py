# -*- coding: utf-8 -*-
"""
"배터리 몇 퍼센트야" 답 (2026-11-12).

배터리를 읽는 도구가 없어서 모델이 숫자를 지어냈다(실제 100%·충전 중인데 "85퍼센트"). 그래서 모델 없이 코드가
psutil.sensors_battery()로 직접 읽어 답한다. psutil 은 윈도우·맥 모두 되므로 윈도우 전용 API 는 쓰지 않는다.

순수 모듈(Qt·플러그인 import 없음). 읽는 쪽(read_battery)과 문장 만드는 쪽(answer)을 나눠서,
테스트에서는 battery 객체(percent / secsleft / power_plugged)를 주입한다.

질문 판별은 보수적이다: 짧은 문장 전체가 "배터리 + 잔량을 묻는 말"일 때만 가로챈다.
("배터리 교체 방법", "노트북 배터리 가격", "핸드폰 배터리 몇 퍼센트야" 같은 건 가로채지 않는다.)
"""
import math
import re

NO_INFO = "이 컴퓨터에서는 배터리 정보를 읽지 못했어요. 배터리가 없는 컴퓨터일 수도 있어요."
LOW_PERCENT = 20
_MAX_SECS = 100 * 3600     # 100시간이 넘는 남은 시간은 믿지 않고 생략한다
_MAX_LEN = 24

# 내 컴퓨터를 가리키는 앞말만 허용한다(휴대폰·이어폰·마우스·차 배터리는 가로채지 않는다)
_PREFIX = r"(?:(?:내|우리|이)?(?:노트북|컴퓨터|랩탑|랩톱|pc|피씨)|내)?(?:지금|현재)?"
_QEND = r"(?:어요|어|니|나요|지|는지|냐|야|이야|이에요|예요|인가요|임)?"
_STATE = r"(?:상태|잔량|잔여량|퍼센트|용량)"
_PCT = r"몇(?:퍼센트|프로|퍼|%)"
_LEFT = r"(?:남았|남아있|남아)"
_ASK = (r"(?:알려(?:줘|줘요|주세요|줄래)|보여(?:줘|줘요|주세요|줄래)|확인(?:해줘|해주세요|해봐)|"
        r"어때(?:요)?|어떻게(?:돼|되)(?:요|니)?)")
_REST = re.compile(
    r"(?:가|는|이|좀)?(?:지금|현재)?(?:"
    rf"{_STATE}(?:가|는|좀)?(?:(?:{_PCT}|몇)?(?:{_LEFT})?|{_ASK}){_QEND}"          # 상태 / 잔량 / 상태 알려줘 / 잔량 몇 퍼센트
    rf"|(?:{_PCT}|몇){_LEFT}?{_QEND}"                                              # 몇 퍼센트(야) / 몇 프로 남았어
    rf"|얼마(?:나)?(?:남았|남아있|있){_QEND}"                                       # 얼마나 남았어
    rf"|{_ASK}"                                                                    # 알려줘
    rf"|(?P<time>남은(?:시간|양|잔량|용량)(?:{_ASK})?{_QEND}|몇시간(?:{_LEFT}|쓸수있|더쓸수있|버텨|버틸수있){_QEND})"   # 남은 시간 / 몇 시간 남았어 / 몇 시간 쓸 수 있어
    r"|(?P<exists>있어(?:요)?|있나요|있니|있냐)"                                       # 배터리 있어?
    r")")
_BATTERY = re.compile(_PREFIX + r"(?:배터리|battery)(?P<rest>.+)")
_CHARGING = re.compile(
    r"(?:지금|현재)?(?:배터리)?(?:충전(?:중|하고있|되고있|되는중|하는중)"
    r"(?:이야|이에요|인가요|이니|인가|이지|맞아|맞나요|이냐|어요|어|니|나요|지|냐)|"
    r"충전기(?:가)?(?:꽂혀있|연결돼있|연결되어있|꽂아져있|꽂았)(?:어요|어|니|나요|지|냐)?)")


def _compact(question) -> str:
    return re.sub(r"[\s!.?~,]+", "", str(question or "")).lower()


def kind(question):
    """질문 종류: 'battery'(잔량) / 'charging'(충전 중인지) / 'time'(남은 시간) / 'exists'(배터리 있는지) / None."""
    text = _compact(question)
    if not text or len(text) > _MAX_LEN:
        return None
    if _CHARGING.fullmatch(text):
        return "charging"
    m = _BATTERY.fullmatch(text)
    if not m:
        return None
    r = _REST.fullmatch(m.group("rest"))
    if not r:
        return None
    if r.group("time"):
        return "time"
    if r.group("exists"):
        return "exists"
    return "battery"


def is_battery_question(question) -> bool:
    return kind(question) is not None


def read_battery():
    """psutil 로 배터리를 읽는다. psutil 이 없거나 배터리가 없거나 읽다 실패하면 None (지어내지 않는다)."""
    try:
        import psutil
        return psutil.sensors_battery()
    except Exception:
        return None


def _percent(battery):
    try:
        value = float(getattr(battery, "percent"))
    except (AttributeError, TypeError, ValueError):
        return None
    if math.isnan(value) or math.isinf(value):
        return None
    return max(0, min(100, int(round(value))))


def _time_left(battery):
    """남은 시간 문구("약 2시간 10분") 또는 None — 음수(충전 중·계산 불가)/무한대/None/비정상 값은 생략."""
    secs = getattr(battery, "secsleft", None)
    if isinstance(secs, bool) or not isinstance(secs, (int, float)):
        return None
    if math.isnan(secs) or math.isinf(secs) or secs <= 0 or secs > _MAX_SECS:
        return None
    minutes = int(secs // 60)
    if minutes <= 0:
        return None
    hours, mins = divmod(minutes, 60)
    if hours and mins:
        return f"약 {hours}시간 {mins}분"
    if hours:
        return f"약 {hours}시간"
    return f"약 {mins}분"


def _state_text(asked, percent, plugged, battery):
    if plugged is True:
        if percent >= 100:
            return "배터리는 100퍼센트예요. 충전기가 연결돼 있고 완충이에요."
        return f"배터리는 {percent}퍼센트이고 충전 중이에요."
    if plugged is False:
        text = f"배터리는 {percent}퍼센트예요. 충전기가 연결돼 있지 않아요."
        left = _time_left(battery)
        if left:
            text += f" {left} 쓸 수 있어요."
        if percent <= LOW_PERCENT:
            text += " 배터리가 얼마 남지 않았어요."
        return text
    return f"배터리는 {percent}퍼센트예요."      # 충전기 연결 여부를 모르면 아는 숫자만 말한다


def answer(question, battery):
    """질문 + battery 객체(또는 None) -> 답 문장. 배터리 질문이 아니면 None."""
    asked = kind(question)
    if asked is None:
        return None
    percent = _percent(battery) if battery is not None else None
    if percent is None:
        return NO_INFO
    plugged = getattr(battery, "power_plugged", None)
    if asked == "charging":
        if plugged is True:     # psutil 은 충전 중과 완충 뒤 연결을 구분하지 못한다 — 100%면 사실대로 말한다
            return ("충전기가 연결돼 있고 완충이에요. 배터리는 100퍼센트예요." if percent >= 100
                    else f"네, 충전 중이에요. 배터리는 {percent}퍼센트예요.")
        if plugged is False:
            return f"아니요, 충전기가 연결돼 있지 않아요. 배터리는 {percent}퍼센트예요."
        return f"충전 중인지는 알 수 없었어요. 배터리는 {percent}퍼센트예요."
    if asked == "time":
        left = _time_left(battery)
        if plugged is True:
            return ("충전기가 연결돼 있고 완충이라 남은 시간은 계산하지 않아요." if percent >= 100
                    else f"충전 중이라 남은 시간은 계산하지 않아요. 배터리는 {percent}퍼센트예요.")
        if plugged is False and left:
            text = f"배터리는 {percent}퍼센트예요. {left} 쓸 수 있어요."
            return text + (" 배터리가 얼마 남지 않았어요." if percent <= LOW_PERCENT else "")
        return f"남은 시간은 알 수 없어요. 배터리는 {percent}퍼센트예요."
    text = _state_text(asked, percent, plugged, battery)
    return ("네, 배터리가 있어요. " + text) if asked == "exists" else text
