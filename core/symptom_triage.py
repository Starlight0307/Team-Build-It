# -*- coding: utf-8 -*-
"""
증상 → 점검 연결 ("PC 응급실", 1단계 A).

사용자가 "컴퓨터가 느려요", "이상한 팝업이 떠요"처럼 증상을 말하면, 이미 있는 점검들을 증상별로
묶어 돌리고 결과를 "확인한 것 / 걸린 것 / 확인 못 한 것 / 원인 후보 / 다음에 할 일"로 정리한다.
AI(LLM)를 부르지 않는 결정론적 경로다(_build_pc_health_check와 같은 이유 — 점수·판정 문장을 LLM이
지어내지 못하게).

설계 원칙
- 순수 함수 위주. Qt·플러그인을 import하지 않고, 점검 함수는 func_map(이름 → callable)으로만 받는다.
- 조치는 하지 않는다. 바꾸는 동작(차단·종료·삭제 등)은 안내 문구로만 말하고, 확인창 흐름
  (_DANGEROUS_FUNCS)은 건드리지 않는다.
- 못 읽은 점검은 "확인하지 못함" 그대로 두고 절대 문제없음으로 바꾸지 않는다. "안전합니다" 같은 단정도 쓰지 않는다.
- 원인 후보는 규칙(점검 결과의 위험/주의 개수 + 몇 가지 수치)으로만 만든다. 확률 표현·추측 생성 없음.
- 개인정보·키·토큰을 만들지도 로그에 남기지도 않는다(점검 결과 문구만 다룬다).

우선순위(core/ai_worker.py의 fast-path 순서와 맞물림)
  1) 기존 fast-path가 먼저다: 하루 브리핑 → PC 종합 점검("종합 점검" 등) → 시스템 상태/"컴 + 느려" CPU 카드 →
     보안 단일 점검·분야 리포트(열린 포트/방화벽 상태/백신 상태/시작프로그램/○○ 보안 종합) → ...
  2) 증상 triage는 그 보안 단일 점검 바로 뒤에서만 한 번 검사한다. 그래서 "컴퓨터가 느려요"처럼 '컴'이 들어간
     느림 표현은 지금처럼 CPU 카드가 처리하고, "PC가 느려요/노트북이 느려요/인터넷이 느려요" 같은 나머지가 여기로 온다.
  3) match_symptom 자체도 기존 fast-path 키워드(YIELD_TO_EXISTING)와 바꾸는 요청·부정 표현이 있으면 None을
     돌려줘서, 어느 자리에서 불려도 기존 경로를 가로채지 않는다.
"""
import inspect
import re
import unicodedata

from core import reply_actions, report_style
from core.reply_actions import Offer, render_choices

# ─────────────────────────────────────────────
# 점검 정보표 — 이름 → (사람이 읽는 이름, 걸렸을 때 원인 후보 문장, 자세히 볼 때 할 말)
# 함수 이름은 plugins/*.py에 실제로 있는 것만 쓴다(없으면 func_map에 안 들어와서 "확인 못 함"으로 보인다).
# ─────────────────────────────────────────────
CHECK_INFO = {
    "detect_suspicious_processes": ("의심스러운 프로세스", "의심 조건에 맞는 프로그램이 실행 중일 수 있어요",
                                    "'의심스러운 프로세스 보여줘'"),
    "scan_startup_items": ("시작프로그램", "컴퓨터를 켤 때 자동으로 실행되는 프로그램 중에 확인이 필요한 것이 있어요",
                           "'시작프로그램 보여줘'"),
    "scan_scheduled_tasks": ("예약 작업", "자동으로 실행되도록 예약된 작업 중에 확인이 필요한 것이 있어요",
                             "'예약 작업 점검해줘'"),
    "scan_suspicious_services": ("백그라운드 서비스", "자동으로 켜지는 백그라운드 프로그램 중에 확인이 필요한 것이 있어요",
                                 "'백그라운드 서비스 점검해줘'"),
    "check_defender_status": ("백신 상태", "백신(Windows 보안) 보호가 약해져 있을 수 있어요", "'백신 상태 알려줘'"),
    "check_dns_settings": ("DNS 설정", "DNS 설정이 평소와 다를 수 있어요(주소 입력이 엉뚱한 곳으로 이어질 수 있어요)",
                           "'DNS 설정 확인해줘'"),
    "check_web_redirection": ("웹 연결 경로(프록시·hosts)", "웹 연결 경로가 바뀌어 있을 수 있어요(다른 사이트로 새는 원인이 될 수 있어요)",
                              "'웹 연결 경로 확인해줘'"),
    "check_wifi_security": ("와이파이 보안", "지금 쓰는 와이파이의 보안 방식이 약할 수 있어요", "'와이파이 보안 확인해줘'"),
    "get_network_connections": ("네트워크 연결", "바깥과 연결된 프로그램 중에 확인이 필요한 것이 있어요", "'네트워크 연결 보여줘'"),
    "get_listening_ports": ("열린 포트", "바깥에서 접속할 수 있는 열린 통로 중에 확인이 필요한 것이 있어요", "'열린 포트 보여줘'"),
    "check_firewall_status": ("방화벽", "방화벽이 꺼져 있거나 느슨하게 설정돼 있을 수 있어요", "'방화벽 상태 알려줘'"),
    "get_login_failures": ("로그인 실패 기록", "최근 로그인 실패 기록이 보여서, 누군가 비밀번호를 맞춰 보고 있을 가능성을 배제할 수 없어요",
                           "'로그인 실패 기록 보여줘'"),
    "check_security_settings": ("PC 보안 설정", "PC 보안 설정 중에 약한 곳이 있어요", "'보안 설정 확인해줘'"),
    "scan_shared_folders": ("공유 폴더", "다른 사람이 접근할 수 있게 공유된 폴더가 있어요", "'공유 폴더 확인해줘'"),
    "check_update_status": ("윈도우 업데이트", "윈도우 업데이트가 밀려 있을 수 있어요", "'업데이트 상태 알려줘'"),
    "scan_browser_extensions": ("브라우저 확장·알림", "브라우저 확장이나 알림을 허용한 사이트 중에 확인이 필요한 것이 있어요",
                                "'브라우저 확장 점검해줘'"),
    "check_ransomware_readiness": ("랜섬웨어 대비 상태", "랜섬웨어에 대비한 설정이나 복구 준비에서 살펴볼 게 있어요",
                                   "'랜섬웨어 대비 상태 점검해줘'"),
    # 판정 없이 수치만 주는 참고용 점검(시스템 정보/CPU 상위) — 문제 있음·없음 개수에 넣지 않는다
    "get_top_cpu_processes": ("CPU를 많이 쓰는 프로그램", "", ""),
    "get_system_info": ("CPU·메모리·디스크 상태", "", ""),
}
# 판정(위험/주의 개수)이 없는 참고용 점검
INFO_CHECKS = frozenset({"get_top_cpu_processes", "get_system_info"})
# check_ransomware_readiness가 안에서 다시 돌리는 점검 — 먼저 돌렸으면 그 결과를 넘겨 중복 실행을 피한다
_REUSABLE = frozenset({"check_update_status", "scan_shared_folders"})

# ─────────────────────────────────────────────
# 증상 정의표
#   title: 사용자에게 보이는 증상 이름
#   patterns: 공백 뺀 소문자 문장에 쓰는 정규식(하나라도 맞으면 증상)
#   checks: 돌릴 점검 함수 이름(순서대로)
#   deferred_checks: 아직 없는 점검(후속 단계) — func_map에 없으면 조용히 건너뛰고, 생기면 자동으로 포함
# ─────────────────────────────────────────────
_PC = r"(?:pc|피씨|컴퓨터|컴터|노트북|랩탑|데스크탑|윈도우|내컴|컴이|컴도|컴은)"
_NET = r"(?:인터넷|와이파이|wifi|wi-fi|네트워크|공유기)"
_GAP = r"(?:가|이|도|는|은|에|에서|를|을)?.{0,8}"   # 증상 낱말과의 짧은 간격(멀리 떨어진 말을 이어 붙이지 않는다)
_VAGUE = r"(?:이상해|이상하|이상한데|이상한것|이상합)"   # 막연한 "이상" — 보수적으로 safe(전체 점검)로 보낸다
_UNKNOWN_PROG = r"(?:모르는|이상한|낯선|처음보는|설치한적없는)(?:프로그램|프로세스)"

SYMPTOMS = {
    "hacked": {
        "title": "해킹당한 것 같아요",
        "patterns": (
            r"해킹(?:당|된|을당|이의심|의심|한것|같|걱정|인가|인지|됐|되었|이된)",
            r"해커(?:가|한테|에게|들|의)",                      # "해커스(학원)", "해커톤"은 걸리지 않는다
            r"(?:악성코드|바이러스|멀웨어|랜섬)(?:에|가|를)?(?:걸린|걸렸|걸린것|감염|있는것같|있을)",
            _PC + r".{0,10}감염",                              # 독감·코로나 "감염"은 PC 말이 없어 걸리지 않는다
            r"(?:악성코드|바이러스|멀웨어)(?:에|가|를)?.{0,6}감염",
            _PC + _GAP + r"(?:몰래|침입|들어와)",
            _UNKNOWN_PROG,                                    # "모르는 프로그램이 실행돼요"
        ),
        "checks": ("check_defender_status", "detect_suspicious_processes", "scan_startup_items", "scan_scheduled_tasks",
                   "scan_suspicious_services", "get_network_connections", "get_login_failures", "check_dns_settings",
                   "check_firewall_status"),
        "deferred_checks": ("scan_browser_extensions",),
    },
    "ransomware": {
        "title": "랜섬웨어가 걱정돼요",
        "patterns": (r"랜섬웨어.{0,12}(?:걱정|무서|무섭|불안|대비|걸릴|걸리|감염|혹시|같|안전|괜찮|대처|겁|일까|일지|인가|인지|아닐까)",
                     r"(?:걱정|무서|무섭|불안|혹시).{0,12}랜섬웨어"),
        # 겹침 정리(2026-11): 업데이트·공유 폴더는 check_ransomware_readiness가 안에 품고 있는 점검이다. 한 번만 돌려
        # (_REUSABLE로 readiness에 넘김) 결과를 재사용하고, 걸렸을 때 '걸린 것'에는 readiness 한 줄 안에서만 말한다
        # (absorbed_by — 같은 발견이 단독 항목과 요약 안에 두 번 나오지 않게). 문제 없을 때는 '문제가 보이지 않은 것'에 그대로 오르고,
        # readiness를 읽지 못했거나 걸리지 않았으면 단독 항목으로 보여 준다(숨기지 않는다).
        "checks": ("check_defender_status", "check_update_status", "check_firewall_status", "scan_shared_folders",
                   "detect_suspicious_processes", "scan_startup_items"),
        "deferred_checks": ("check_ransomware_readiness",),
        "absorbed_by": {"check_ransomware_readiness": ("check_update_status", "scan_shared_folders")},
    },
    "login": {
        "title": "누가 내 PC에 로그인하려는 것 같아요",
        "patterns": (
            r"(?:누가|누군가|남이|다른사람이?|모르는사람이?).{0,10}(?:로그인|접속|들어오|침입|원격)",
            r"(?:누가|누군가|남이|다른사람이?|모르는사람이?).{0,10}(?:쓰는|쓰고있|사용하는|사용하고있|만지)",
            r"(?:접속|들어오|로그인)(?:하는|한|하려는)사람",
            r"로그인(?:을|이)?(?:시도|실패)",
            r"무단접속|무단로그인",
            r"원격접속(?:이|을)?(?:의심|이상|같)",
            r"비밀번호(?:를|가)?(?:계속|자꾸)?(?:틀|맞춰)",
        ),
        "checks": ("get_login_failures", "get_listening_ports", "check_firewall_status", "scan_shared_folders",
                   "check_security_settings", "get_network_connections"),
        "deferred_checks": (),
    },
    # hacked·popup이 겹쳐 쓰는 detect_suspicious_processes·scan_startup_items는 spec을 그대로 둔다 —
    # 증상마다 해당 점검이 필요하고, 같은 발견이 대화 안에서 되풀이되는 것은 security_session의 '이미 알려 드린 것' 접기가 맡는다.
    "popup": {
        "title": "이상한 팝업·광고가 떠요",
        "patterns": (
            r"(?:팝업|광고)(?:가|이|창|들이)?.{0,8}(?:떠|뜨|계속|자꾸|나와|나타|많이|이상)",
            r"이상한(?:창|광고|팝업|사이트|페이지)",
            r"자꾸(?:다른|엉뚱한)(?:사이트|페이지|곳)",
            r"(?:시작페이지|홈페이지|검색엔진|검색창)(?:이|가)?(?:바뀌|바꼈|이상)",
        ),
        "checks": ("check_web_redirection", "check_dns_settings", "detect_suspicious_processes", "scan_startup_items",
                   "scan_scheduled_tasks"),
        "deferred_checks": ("scan_browser_extensions",),
    },
    "network": {
        "title": "인터넷이 이상해요",
        "patterns": (
            _NET + _GAP + r"(?:이상|안돼|안되|안열|안떠|끊|느려|느리|느린|불안정|오락가락|먹통|연결이?안)",
        ),
        "checks": ("check_dns_settings", "check_web_redirection", "check_wifi_security", "get_network_connections",
                   "check_firewall_status"),
        "deferred_checks": (),
    },
    "slow": {
        "title": "컴퓨터가 느려요",
        "patterns": (
            _PC + _GAP + r"(?:느려|느리|느린|버벅|렉|끊겨|무거워|답답|멈춰|멈췄|멈추|먹통)",
        ),
        # 윈도우 업데이트 지연은 '느려짐'의 원인으로 약하다(업데이트 얘기는 시작 알림·개별 점검이 맡는다) — 뺐다.
        "checks": ("get_top_cpu_processes", "get_system_info", "detect_suspicious_processes", "scan_startup_items",
                   "scan_scheduled_tasks", "scan_suspicious_services"),
        "deferred_checks": (),
    },
    "safe": {
        "title": "내 PC가 안전한지 궁금해요",
        "patterns": (
            _PC + _GAP + r"(?:안전한지|안전한가|안전해|안전한거|안전할까|안전하냐|괜찮은지|괜찮은가|문제없는지|이상없는지|이상없나)",
            _PC + _GAP + _VAGUE,    # "컴퓨터가 이상해요" — 막연한 이상은 전체 점검(safe)으로
        ),
        "checks": ("check_defender_status", "check_firewall_status", "check_update_status", "detect_suspicious_processes",
                   "scan_startup_items", "check_dns_settings", "get_login_failures", "scan_shared_folders",
                   "check_security_settings"),
        "deferred_checks": ("scan_browser_extensions", "check_ransomware_readiness"),
    },
}
# 위에서부터 맞는 첫 증상을 고른다. 구체적인 보안 증상이 넓은 증상보다 먼저다
# ("컴퓨터 느린데 랜섬웨어일까"는 ransomware, 막연한 "이상"은 맨 뒤 safe).
_ORDER = ("hacked", "ransomware", "login", "popup", "network", "slow", "safe")
_COMPILED = {sid: tuple(re.compile(p) for p in SYMPTOMS[sid]["patterns"]) for sid in _ORDER}

# ── 가로채지 않을 말(정규식, 공백을 뺀 문장 기준). 묶음마다 이유를 적는다 ──
# 부분 문자열이 진짜 증상을 지우지 않도록 "디스크"·"일정"·"게임" 같은 흔한 낱말 단독은 쓰지 않고 의도가 드러나는 말만 쓴다.
_ASK = r"(?:해줘|해주|해봐|해라|해줄|하고싶|해볼|해야|할래|시켜)"
# 1) 바꾸는 요청: 확인창을 거쳐야 하는 동작이라 기존 경로에 맡긴다.
#    "추가/등록/변경" 같은 짧은 낱말은 요청 꼬리(_ASK)가 붙을 때만 막는다("광고가 추가로 계속 떠요"는 증상)
_EXCLUDE_CHANGE = (
    r"막아", r"차단", r"켜줘", r"켜주", r"꺼줘", r"꺼주", r"끄(?:기|고|자|라|면)", r"지워", r"삭제", r"풀어", r"허용", r"신뢰",
    r"제거", r"없애", r"바꿔", r"사용안함", r"종료(?:해|시켜)",
    r"(?:추가|등록|변경|설정|수정|해제)" + _ASK, r"만들어(?:줘|주)",
    # 재부팅·초기화 요청도 명령형이다("재부팅해도 느려요"는 증상이라 꼬리가 붙은 경우만)
    r"(?:재부팅|재시작|리셋|초기화|다시시작)(?:좀)?" + _ASK,
)
# 2) 부정: "~하지 마", "~말고"
_EXCLUDE_NEGATION = (r"하지마", r"하지말", r"말고", r"필요없", r"않아도", r"안해도")
# 3) 방법·정보·뉴스 질문: 증상이 아니라 정보를 묻는 말
_EXCLUDE_INFO = (
    r"방법", r"예방", r"방지", r"대처", r"어떻게(?:해|하면|막|대처|복구|고치|풀|없애)", r"뉴스", r"알림이?(?:왔|떴|와)",
    r"쓰는법", r"하는법", r"팁(?:알려|좀)|꿀팁", r"추천", r"안전하게", r"(?:당했|걸렸|됐|했)을때",
    r"(?:해킹|랜섬웨어|바이러스|악성코드)(?:이|가)?란", r"뜻(?:이|은)?뭐|무슨뜻", r"차이(?:점|가뭐|는뭐)",
)
# 4) 다른 의도: PC 정리·속도 측정·기록 조회는 기존 fast-path나 단일 점검 경로에 맡긴다.
_EXCLUDE_INTENT = (
    r"정리", r"최적화", r"청소", r"디스크(?:정리|용량|공간|청소|포맷)", r"용량(?:정리|확보|비워|늘려)", r"저장공간(?:정리|확보)",
    r"측정", r"벤치", r"핑테스트", r"속도(?:를|좀)?(?:테스트|체크|재)",
    r"(?:기록|내역|목록|횟수|몇번|몇개)(?:좀|을|를)?(?:보여|알려|확인|조회)",
    r"일정(?:알려|보여|잡|추가|등록|확인|조회)", r"게임(?:추천|순위|사줘|다운|설치)",
)
# 5) PC와 상관없는 주제나 물리 증상: 가격·시세·영상 같은 말과, 소리·냄새 같은 하드웨어 증상
_EXCLUDE_TOPIC = (
    r"시세", r"가격", r"최저가", r"강의", r"영상", r"주식", r"쇼핑", r"배터리", r"피씨방",
    r"이상한(?:소리|냄새|소음|진동|불빛|열)",
)
_EXCLUDE = _EXCLUDE_CHANGE + _EXCLUDE_NEGATION + _EXCLUDE_INFO + _EXCLUDE_INTENT + _EXCLUDE_TOPIC

# PC가 아니라 계정·서비스 이야기 — PC 말이 함께 없으면 hacked/login/ransomware/popup으로 가로채지 않는다
_SERVICE_CONTEXT = re.compile(
    r"계정|카드|비밀번호|패스워드|개인정보|정보유출|네이버|카카오|카톡|인스타|페이스북|트위터|구글|은행|통장|이메일|메일|sns|앱|서비스"
    r"|일정|유튜브|넷플릭스|게임|쿠팡|배달")
_SERVICE_SITE = re.compile(_SERVICE_CONTEXT.pattern + r"|사이트")   # 해킹/로그인은 "사이트"도 서비스 문맥으로 본다
# PC가 아닌 기기(휴대폰·태블릿 등) — PC 말 없이 이 말만 있으면 PC 증상으로 보지 않는다
_NOT_PC = re.compile(r"스마트폰|핸드폰|휴대폰|휴대전화|아이폰|갤럭시|아이패드|태블릿|맥북|내폰|폰이|폰도|폰에|폰으로|폰은")
_PC_CONTEXT = re.compile(_PC)
_PC_ONLY_SYMPTOMS = ("login",)   # 이 증상은 PC 말이 있어야 인정("누가 로그인 시도" 같은 말은 서비스 알림일 수 있다)
# "원인 점검/원인 찾아줘"는 slow 증상으로(기존 CPU 카드가 '컴+느려'를 가져가기 전에 ai_worker가 이 표현을 보고 양보한다)
CAUSE_REQUEST = re.compile(r"원인\s*(?:을\s*)?(?:점검|찾|분석|진단)")
# 기존 fast-path(종합 점검·분야 리포트·단일 점검)의 키워드 — ai_worker의 같은 목록과 test_symptom_triage가 맞는지 확인한다.
YIELD_TO_EXISTING = (
    "종합점검", "전체점검", "다점검", "전부점검", "총점검", "종합진단", "전체진단", "한번에점검", "전체적으로점검",
    "네트워크보안종합", "네트워크보안점검", "네트워크보안리포트",
    "시스템보안종합", "시스템보안점검", "시스템보안리포트",
    "악성코드종합", "악성코드점검", "악성코드리포트", "악성코드검사",
    "열린포트", "열려있는포트", "방화벽상태", "방화벽확인", "방화벽켜져있", "방화벽꺼져있",
    "백신상태", "백신확인", "백신켜져있", "디펜더상태", "defender상태", "xprotect", "게이트키퍼", "gatekeeper",
    "파일볼트", "filevault", "mac기본보안", "시작프로그램",
    # PC 응급실 새 점검(ai_worker 2.6b) — 이름을 콕 집은 요청은 그 점검 하나만
    "랜섬웨어대비상태", "랜섬웨어대비점검", "랜섬웨어대비확인", "랜섬웨어대비준비", "랜섬웨어준비상태", "랜섬웨어준비점검",
    "랜섬웨어준비확인", "랜섬웨어상태", "랜섬웨어점검", "랜섬웨어대비어때",
    "브라우저확장점검", "브라우저확장확인", "브라우저확장검사", "브라우저확장목록", "확장프로그램점검", "확장프로그램확인",
    "확장프로그램검사", "확장프로그램목록", "받은파일목록", "다운로드한파일목록", "받은파일점검", "받은파일검사",
    "다운로드한파일점검",
)


def _normalize(text) -> str:
    return re.sub(r"\s+", "", str(text or "").lower())


def match_symptom(text):
    """문장이 '증상 설명'이면 증상 id(SYMPTOMS의 키), 아니면 None.
    바꾸는 요청·부정·정보 질문·다른 의도(정리/측정/기록 조회), 그리고 기존 fast-path 키워드가 있으면 None."""
    t = _normalize(text)
    if not t:
        return None
    if any(re.search(x, t) for x in _EXCLUDE):
        return None
    if any(k in t for k in YIELD_TO_EXISTING):
        return None
    # ai_worker의 _SECURITY_COMBOS와 같은 조합("포트 + 방화벽", "프로세스 + 시작프로그램")
    if "포트" in t and "방화벽" in t:
        return None
    has_pc = bool(_PC_CONTEXT.search(t))
    if not has_pc and _NOT_PC.search(t):
        return None     # 휴대폰·태블릿 이야기
    for sid in _ORDER:
        if not any(p.search(t) for p in _COMPILED[sid]):
            continue
        if sid in _PC_ONLY_SYMPTOMS and not has_pc:
            continue
        if not has_pc:
            # 계정·서비스 이야기는 PC 증상이 아니다(popup은 "사이트"가 증상 말이라 제외하고 본다)
            ctx = _SERVICE_CONTEXT if sid == "popup" else _SERVICE_SITE
            if sid in ("hacked", "login", "ransomware", "popup") and ctx.search(t):
                continue
        return sid
    return None


# ─────────────────────────────────────────────
# 결과 읽기 — plugins의 CheckResult는 속성(critical/warning/unknown/summary)으로 읽는다(클래스 import 안 함)
# ─────────────────────────────────────────────
def _counts(result):
    """(위험, 주의, 확인 못 함) 또는 판정 정보가 없으면 None"""
    if not isinstance(result, str):
        return None
    try:
        vals = tuple(getattr(result, n, None) for n in ("critical", "warning", "unknown"))
    except Exception:    # 속성 읽기에서 오류가 나는 객체는 '판정 정보 없음'
        return None
    ok = all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in vals)
    return vals if ok else None


def _summary(result) -> str:
    try:
        s = getattr(result, "summary", "")
    except Exception:
        return ""
    return s.strip() if isinstance(s, str) else ""


# 플러그인 요약 끝에 붙은 "~해 주세요 / ~라고 하면 ~ / '○○ 보여줘'" 같은 사용자에게 시키는 문장은 보여 주지 않는다.
# (비서는 사용자에게 행동·발화를 시키지 않는다 — 사실 문장과 판정 개수는 그대로 둔다)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?。])\s+")
_ORDER_SENTENCE = re.compile(
    # 명령·부탁 어미(…세요/…십시오/…주세요/…바랍니다 — 해 주세요·눌러 주세요·꺼 주세요·열어 주세요·실행해 주세요·
    # 입력해 주세요·설정해 주세요·확인하세요·지우지 마세요·호출하세요 …을 모두 포함), 발화를 시키는 말, 클릭 안내, 따옴표 명령
    r"세요|십시오|바랍니다|주시기|해\s?보|말해|말씀|라고\s?(?:하면|말)|직접\s|우클릭|클릭|눌러|누르|"
    r"'[^']*(?:줘|해줘|보여줘|알려줘|해 줘)'|\"[^\"]*(?:줘|해줘|보여줘|알려줘)\"|확인해\s?봐")
_CLAUSE_SPLIT = re.compile(r"\s+—\s+")


def _drop_orders(text):
    """요약에서 사용자에게 시키는 문장만 문장 단위로 빼고, 제3자 '루미가'는 '제가'로. 전부 빠지면 ''.
    한 문장 안에서 ' — '로 이어진 앞쪽이 사실이고 뒤쪽만 지시이면 사실 부분은 남긴다."""
    if not isinstance(text, str) or not text:
        return ""
    kept = []
    for s in _SENTENCE_SPLIT.split(text.strip()):
        if not s:
            continue
        if not _ORDER_SENTENCE.search(s):
            kept.append(s)
            continue
        parts = [p for p in _CLAUSE_SPLIT.split(s) if p and not _ORDER_SENTENCE.search(p)]
        if len(parts) > 0 and len(_CLAUSE_SPLIT.split(s)) > 1:
            kept.append(" — ".join(parts).rstrip(" ,;") + ("" if parts[-1].endswith((".", "!", "?")) else "."))
    return " ".join(kept).replace("루미가 ", "제가 ").replace("루미는 ", "제가 ").strip()


_TOP_CPU = re.compile(r"1\. (?P<name>.+?) \(점유율: (?P<pct>[\d.]+)%\)")
_RAM = re.compile(r"메모리.*?총 ([\d.]+)GB 중 ([\d.]+)GB 사용")
_DISK = re.compile(r"디스크.*?총 ([\d.]+)GB 중 ([\d.]+)GB 여유")
CPU_HIGH_PERCENT = 30   # get_top_cpu_processes는 코어 수로 나눈 값이라 한 프로그램이 이 정도면 많이 쓰는 것
RAM_HIGH_PERCENT = 85
DISK_LOW_PERCENT = 10


def _as_float(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _top_cpu(raw):
    m = _TOP_CPU.search(raw or "")
    if not m:
        return None
    pct = _as_float(m.group("pct"))
    return (m.group("name"), pct) if pct is not None else None


def _ram_percent(raw):
    m = _RAM.search(raw or "")
    if not m:
        return None
    total, used = _as_float(m.group(1)), _as_float(m.group(2))
    return used / total * 100 if total and used is not None else None


def _disk_free_percent(raw):
    m = _DISK.search(raw or "")
    if not m:
        return None
    total, free = _as_float(m.group(1)), _as_float(m.group(2))
    return free / total * 100 if total and free is not None else None


def _info_readable(name, raw) -> bool:
    """참고용 점검의 결과를 읽을 수 있는 모양인지(못 읽으면 '확인하지 못한 것'으로 올린다)"""
    if not raw or not raw.strip():
        return False
    if name == "get_top_cpu_processes":
        return _top_cpu(raw) is not None or "없습니다" in raw    # "CPU를 쓰는 프로세스가 없습니다"도 정상 결과
    if name == "get_system_info":
        return _ram_percent(raw) is not None or _disk_free_percent(raw) is not None
    return True


# 판정은 문제없음(위험·주의 0)이지만 summary에 "N번/건/회" 기록이 있는 경우 — "문제 없음"으로 묶지 않고 그대로 보여 준다
_HAS_RECORD = re.compile(r"\d+\s*(?:번|건|회|개)")

_MARKS = ("🚨", "⚠️", "⚠", "❔")
_HIDDEN = "(이름 숨김)"
_NAME_MAX = 40       # 외부에서 온 이름(프로세스·파일·확장 이름)은 짧게만 보여 준다
_REASON_MAX = 60
_ANSI = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b[@-_]")
_ANSI_LEFT = re.compile(r"\[[0-9;]{1,12}m")      # ESC만 지워지고 남은 '[31m' 꼴
# 이름이 모델에게 지시문처럼 읽히지 않게 — NFKC(전각→반각)로 맞춘 소문자 글에서 찾는다
_INJECTION = re.compile(
    r"ignore\s+(?:all\s+|the\s+|any\s+)?(?:previous|prior|above|earlier)|disregard|forget\s+(?:all|the|your)"
    r"|system\s*prompt|you\s+(?:must|are\s+now|should)|^\s*(?:system|assistant|user)\s*:"
    r"|say\s+(?:it\s+is\s+|that\s+)?safe|tell\s+(?:the\s+)?user"
    r"|이전\s*(?:의\s*)?(?:모든\s*)?(?:지시|명령|지침|프롬프트)|(?:지시|명령|지침)(?:를|을|문을|사항을)?\s*무시"
    r"|시스템\s*프롬프트|안전하다고\s*(?:말|답|알려)|위험\s*없다고\s*(?:말|답|알려)")
# 비밀값이 들어 있을 만한 모양만 — 'secret.txt'·'tokenizer' 같은 보통 이름은 걸리지 않게 값이 붙는 꼴만 본다
_SENSITIVE = re.compile(
    r"(?i)(?:passw(?:or)?d|pwd|token|secret|api[_-]?key|access[_-]?key|bearer|비밀번호|토큰)\s*[=:]\s*\S"
    r"|\b[0-9a-f]{32,}\b|[A-Za-z0-9+/_-]{40,}={0,2}")
_URL_SECRET_QUERY = re.compile(r"(?i)[?&#](?:key|sig|signature|code|token|access_token|auth|apikey|api_key)=\S*")
_URL = re.compile(r"((?:https?|ftp)://[^\s?#\"'<>]+)[?#]\S*", re.I)
# 경로 → 파일 이름만. 드라이브(C:\), 공유(\\서버\), %환경변수%\ 로 시작하는 경로를 마지막 '\' 뒤만 남긴다
_PATH = re.compile(r"(?:(?<![A-Za-z0-9])[A-Za-z]:[\\/](?!/)|\\\\[^\\/\s\"'<>|]+[\\/]|%[A-Za-z_]+%[\\/])[^\"<>|*\r\n]*")


def _cut(s: str, n: int) -> str:
    """길이 제한 — 가능하면 공백(단어 경계)에서 자른다"""
    if len(s) <= n:
        return s
    head = s[:n - 1]
    sp = head.rfind(" ")
    if sp >= n * 0.6:
        head = head[:sp]
    return head.rstrip() + "…"


def _strip_ctrl(s: str) -> str:
    """제어문자(Cc)·서식문자(Cf: U+202E·U+200B 등)·줄/문단 구분(Zl·Zp)·ANSI 꾸밈 열을 공백으로"""
    if "\x1b" in s:
        s = _ANSI.sub(" ", s).replace("\x1b", " ")
        s = _ANSI_LEFT.sub(" ", s)
    return "".join(" " if unicodedata.category(c)[0] == "C" or unicodedata.category(c) in ("Zl", "Zp") else c
                   for c in s)


def _path_to_name(m) -> str:
    return re.split(r"[\\/]", m.group(0).rstrip("\\/"))[-1]


def _clean_detail(s) -> str:
    """외부 문자열 정리 — 제어·서식 문자와 ANSI 제거, 경로는 파일 이름만, URL은 ?/# 앞까지, 공백 정리."""
    s = _strip_ctrl(s if isinstance(s, str) else "")
    for m in _MARKS:
        s = s.replace(m, " ")
    s = _URL.sub(r"\1", s)
    s = _URL_SECRET_QUERY.sub("", s)
    s = _PATH.sub(_path_to_name, s)
    return re.sub(r"\s+", " ", s).strip(" -:·")


def _looks_like_instruction(s: str) -> bool:
    return bool(_INJECTION.search(unicodedata.normalize("NFKC", s).lower()))


def _safe_name(s, limit=_NAME_MAX) -> str:
    """사용자에게 보일 이름 — 비밀값 모양이나 지시문처럼 읽히는 말이 들어 있으면 '(이름 숨김)'으로 바꾼다."""
    s = _clean_detail(s)
    if not s:
        return ""
    n = unicodedata.normalize("NFKC", s)
    if _SENSITIVE.search(n) or _looks_like_instruction(s):
        return _HIDDEN
    return _cut(s, limit)


def _short_item(item: str) -> str:
    """'[분류] 이름 → C:/…/파일.exe' 꼴이면 경로는 파일 이름만 남긴다(사용자 이름이 든 경로를 줄이려고)."""
    if "→" not in item:
        return item
    left, right = item.split("→", 1)
    right = right.strip()
    q = re.match(r'"([^"]+)"', right)      # 따옴표로 감싼 경로를 먼저 뽑고, 아니면 ' /옵션' 앞까지만 경로로 본다
    path = q.group(1) if q else re.split(r"\s+/", right, maxsplit=1)[0].split(" (")[0]
    base = re.split(r"[\\/]", path.strip())[-1].strip()
    return f"{left.strip()} → {base}" if base else left.strip()


def _format_item(item: str) -> str:
    """'[분류] 이름 → 파일'에서 이름 부분을 「…」로 감싸 데이터로 보이게 한다"""
    item = _short_item(item)
    m = re.match(r"\s*(\[[^\]]{0,40}\])\s*(.*)", item)
    cat, rest = (_clean_detail(m.group(1)), m.group(2)) if m else ("", item)
    name = _safe_name(rest)
    if not name:
        return ""
    shown = name if name == _HIDDEN else f"「{name}」"
    return f"{cat} {shown}".strip()


def _details(raw, limit=2):
    """점검 결과 글에서 위험·주의 항목의 핵심 한 줄을 최대 limit개 뽑는다. 점검마다 글 모양이 달라서
    알아보지 못하면 빈 목록(→ 기존 표시 그대로). 어떤 오류도 밖으로 내지 않는다."""
    try:
        if not isinstance(raw, str):
            return []
        lines = raw.splitlines()
        out = []
        in_block = False
        i = 0
        while i < len(lines) and len(out) < limit:
            ln = lines[i]
            st = ln.strip()
            if not st:
                in_block = False
            elif not ln[:1].isspace() and any(m in st for m in _MARKS) and st.endswith(":"):
                in_block = True      # '🚨 의심 항목 1개:' 같은 머리말 — 아래 '- ' 항목들이 걸린 것
            elif in_block and st.startswith("- "):
                item = _format_item(st[2:])
                reason = ""
                if i + 1 < len(lines):
                    nxt = lines[i + 1].strip()
                    if any(nxt.startswith(m) for m in _MARKS) and not nxt.endswith(":"):
                        reason = _clean_detail(nxt)
                        if _SENSITIVE.search(unicodedata.normalize("NFKC", reason)) or _looks_like_instruction(reason):
                            reason = ""
                        reason = _cut(reason, _REASON_MAX)
                if item:
                    out.append(f"{item} — {reason}" if reason else item)
            elif in_block and not ln[:1].isspace():
                in_block = False
            i += 1
        if not out:      # 머리말 없이 '🚨 …' / '⚠️ …' 한 줄로 알리는 점검
            for ln in lines:
                st = ln.strip()
                if any(st.startswith(m) for m in _MARKS) and not st.endswith(":") and not st.startswith(("조치", "설명")):
                    d = _safe_name(st, 80)
                    if d and d not in out:
                        out.append(d)
                    if len(out) >= limit:
                        break
        return out[:limit]
    except Exception:
        return []


def _accepts_precomputed(func) -> bool:
    """점검 함수가 precomputed(이미 돌린 점검 결과 재사용) 인자를 받는지 — 모르면 False(그냥 부른다)."""
    try:
        return "_precomputed" in inspect.signature(func).parameters
    except (TypeError, ValueError):
        return False


def _run_one(name, func_map, reuse=None):
    """점검 하나를 돌려 상태를 돌려준다. status: problem / ok / unknown / info"""
    label = CHECK_INFO.get(name, (name, "", ""))[0]
    func = func_map.get(name)
    if func is None:
        return {"name": name, "label": label, "status": "unknown", "reason": "이 점검 기능이 설치되어 있지 않아요",
                "critical": 0, "warning": 0, "unknown": 0, "summary": "", "raw": ""}
    try:
        result = func(_precomputed=reuse) if reuse and _accepts_precomputed(func) else func()
    except Exception as e:
        print(f"[증상 점검] {name} 실행 오류: {type(e).__name__}")   # 내용(경로·계정 등)은 남기지 않는다
        return {"name": name, "label": label, "status": "unknown", "reason": "점검하는 중에 문제가 생겼어요",
                "critical": 0, "warning": 0, "unknown": 0, "summary": "", "raw": ""}
    raw = result if isinstance(result, str) else ""
    raw_summary = _summary(result)
    base = {"name": name, "label": label, "raw": raw, "summary": _drop_orders(raw_summary), "reason": "", "obj": result}
    if name in INFO_CHECKS:
        if not _info_readable(name, raw):
            return {**base, "status": "unknown", "reason": "결과를 읽지 못했어요", "critical": 0, "warning": 0, "unknown": 0}
        return {**base, "status": "info", "critical": 0, "warning": 0, "unknown": 0}
    counts = _counts(result)
    if counts is None:
        # 판정 정보가 없는 결과는 문제없음으로 읽지 않는다
        return {**base, "status": "unknown", "reason": "결과를 판정하지 못했어요", "critical": 0, "warning": 0, "unknown": 0}
    crit, warn, unk = counts
    if crit or warn:
        return {**base, "status": "problem", "critical": crit, "warning": warn, "unknown": unk}
    if unk:
        return {**base, "status": "unknown", "reason": base["summary"] or "읽지 못한 부분이 있어요",
                "critical": 0, "warning": 0, "unknown": unk}
    note = bool(raw_summary and _HAS_RECORD.search(raw_summary))      # 기록 유무는 걸러내기 전 원문으로 본다
    return {**base, "status": "ok", "note": note, "critical": 0, "warning": 0, "unknown": 0}


def _ok_text(r):
    """문제없음 목록의 항목 이름 — 기록이 없다는 점검은 '기록 없음'을 덧붙여 '못 본 것'과 구분한다"""
    if r["name"] == "get_login_failures" and re.search(r"기록이\s*없", r["summary"]):
        return f"{r['label']}(실패 기록 없음)"
    return r["label"]


def _causes(results):
    """규칙 기반 원인 후보 [(문장, 근거)] — 점검 결과의 판정 개수와 수치로만 만든다."""
    by = {r["name"]: r for r in results}
    out = []

    def flagged(n):
        return n in by and by[n]["status"] == "problem"

    def evidence(n):
        r = by[n]
        return f"{r['label']} 점검에서 위험 {r['critical']}개·주의 {r['warning']}개가 나왔어요"

    top = _top_cpu(by["get_top_cpu_processes"]["raw"]) if "get_top_cpu_processes" in by and by["get_top_cpu_processes"]["status"] == "info" else None
    cpu_high = top is not None and top[1] >= CPU_HIGH_PERCENT
    proc = flagged("detect_suspicious_processes")
    if proc and cpu_high:
        out.append(("백그라운드에서 CPU를 많이 쓰는 프로그램이 있어요",
                    f"{evidence('detect_suspicious_processes')}, 그리고 {top[0]}이(가) CPU를 {top[1]:g}% 쓰고 있어요"))
    elif cpu_high:
        out.append((f"CPU를 많이 쓰는 프로그램({top[0]})이 있어요", f"CPU 사용량 1위가 {top[0]} {top[1]:g}%예요"))
    elif proc:
        out.append((CHECK_INFO["detect_suspicious_processes"][1], evidence("detect_suspicious_processes")))

    info = by.get("get_system_info")
    if info and info["status"] == "info":
        ram = _ram_percent(info["raw"])
        if ram is not None and ram >= RAM_HIGH_PERCENT:
            out.append(("메모리가 거의 차 있어서 느려졌을 수 있어요", f"메모리를 {ram:.0f}% 쓰고 있어요"))
        free = _disk_free_percent(info["raw"])
        if free is not None and free < DISK_LOW_PERCENT:
            out.append(("저장 공간(디스크)이 거의 차 있어요", f"디스크 여유 공간이 {free:.0f}%예요"))

    for name in ("scan_startup_items", "scan_scheduled_tasks", "scan_suspicious_services", "check_defender_status",
                 "check_dns_settings", "check_web_redirection", "check_wifi_security", "get_network_connections",
                 "get_listening_ports", "check_firewall_status", "get_login_failures", "check_security_settings",
                 "scan_shared_folders", "check_update_status"):
        if flagged(name):
            out.append((CHECK_INFO[name][1], evidence(name)))
    # 이름이 표에 없는 후속 점검(deferred)이 걸린 경우도 빠뜨리지 않는다
    for r in results:
        if r["status"] == "problem" and r["name"] not in CHECK_INFO:
            out.append((f"{r['label']}에서 확인이 필요한 항목이 나왔어요", evidence(r["name"])))
    return out


def _safe_call(fn, *args):
    """콜백이 없거나 오류가 나도 점검은 계속한다(오류 내용은 남기지 않는다)"""
    if fn is None:
        return False
    try:
        return fn(*args)
    except Exception:
        return False


def run_triage(symptom_id, func_map, progress=None, should_stop=None):
    """문장만 돌려주는 순수 래퍼 — 제안 선택지(Offer)까지 필요하면 run_triage_with_offers를 쓴다."""
    return run_triage_with_offers(symptom_id, func_map, progress, should_stop)[0]


_MAX_OFFERS = 3


def all_read_only(funcs) -> bool:
    """제안 도구가 전부 읽기 전용 점검이면 True — 그러면 '응' 한 번에 전부 보여 주므로 한 번에 묻는 문장을 쓴다.
    읽기 전용 목록(reply_actions.READ_ONLY_CHECKS)이 없거나 하나라도 거기 없으면 False(번호 선택지 형식 유지)."""
    ro = getattr(reply_actions, "READ_ONLY_CHECKS", None)
    funcs = list(funcs)
    return bool(ro) and len(funcs) >= 2 and all(fn in ro for fn in funcs)


def _batchim(word: str) -> bool:
    ch = str(word or "")[-1:]
    return "가" <= ch <= "힣" and (ord(ch) - 0xAC00) % 28 != 0


def join_names(names) -> str:
    """이름 나열 — 2개면 'A와/과 B', 3개 이상이면 'A·B·C'."""
    names = list(names)
    if len(names) == 2:
        return f"{names[0]}{'과' if _batchim(names[0]) else '와'} {names[1]}"
    return "·".join(names)


def obj_josa(word: str) -> str:
    """목적격 조사('을'/'를')."""
    return "을" if _batchim(word) else "를"


def triage_offers(results, func_map):
    """점검 결과 목록에서 '더 자세히 보여 드릴까요?' 제안(Offer)을 만든다. 걸린 점검(없으면 못 읽은 점검)의
    읽기 도구만 가리키고 인자는 없다. 바꾸는 조치(차단·종료)는 여기서 제안하지 않는다 — 자세히 본 결과가 한다."""
    if not isinstance(func_map, dict):
        return []
    def pick(status):
        return [r for r in results if r["status"] == status and r["name"] in CHECK_INFO and r["name"] in func_map][:_MAX_OFFERS]
    chosen = pick("problem")
    verb = "자세히 보기"
    if not chosen:
        chosen, verb = pick("unknown"), "다시 확인하기"
    return [Offer(label=f"{r['label']} {verb}", func=r["name"], args={}) for r in chosen]


_ENDINGS = (
    (re.compile(r"있을 수 있어요$"), "있을 가능성"), (re.compile(r"일 수 있어요$"), "일 가능성"),
    (re.compile(r" 수 있어요$"), " 수 있음"), (re.compile(r"있어요$"), "있음"), (re.compile(r"없어요$"), "없음"),
    (re.compile(r"있었어요$"), "있었음"), (re.compile(r"했어요$"), "함"), (re.compile(r"나왔어요$"), "나옴"), (re.compile(r"못했어요$"), "못 함"), (re.compile(r"않았어요$"), "않음"),
    (re.compile(r"(?:이에요|예요)$"), ""), (re.compile(r"해요$"), "함"),
)


def _noun(text) -> str:
    """서술형 한 문장을 보고서용 명사구로(어미만 바꾸고 의미는 그대로 — 못 바꾸면 원문 그대로)."""
    from core.startup_security import _nominal
    t = _nominal(re.sub(r"\s+", " ", str(text or "")).strip())
    for pat, rep in _ENDINGS:
        if pat.search(t):
            return pat.sub(rep, t).strip()
    return t


def _first_sentence(text) -> str:
    return _SENTENCE_SPLIT.split(str(text or "").strip(), maxsplit=1)[0].strip().rstrip(".。")


def _unknown_reason(reason) -> str:
    r = str(reason or "")
    if "설치되어 있지 않" in r:
        return "점검 기능 미설치"
    if "관리자 권한" in r:
        return "관리자 권한 필요"
    if "문제가 생겼" in r:
        return "점검 중 오류"
    if "판정하지 못" in r or "읽지 못" in r:
        return "결과를 읽지 못함"
    return report_style._clip(_noun(_first_sentence(_drop_orders(r))), 25)


def _cause_note(text, why):
    """원인 후보 → ('추정 원인' 한 줄). 점검 표에 이미 있는 항목별 원인은 항목 줄이 말하므로 빼고 수치 기반만 쓴다."""
    m = re.search(r"(\S+?)이\(가\) CPU를 ([\d.]+)% 쓰고", why)
    detail = f"{m.group(1)} CPU {m.group(2)}%" if m else ""
    line = _noun(text)
    return f"ℹ️ 추정 원인 — {line}" + (f"({detail})" if detail else "")


_MAX_ITEMS = 6
REPORT_TITLES = {"hacked": "해킹 의심 점검", "ransomware": "랜섬웨어 점검", "login": "로그인 시도 점검", "popup": "광고·팝업 점검",
                 "network": "인터넷 이상 점검", "slow": "느림 점검", "safe": "PC 종합 점검"}
_JUNK_OK_NAME = re.compile(r"^\d|나머지|^외 ")


def _fallback_item(r, mark):
    """영역 모듈이 못 알아본 원문 — 이름과 핵심 상태만 한 줄로(상세 줄 없음, 단어 경계에서만 자름)."""
    if mark == "❔":
        return report_style.Item("❔", r["label"], "확인 못 함", _unknown_reason(r["reason"] or "읽지 못했어요"))
    for d in _details(r.get("raw")):      # 걸린 항목의 이름과 핵심 이유(예: 「프로그램.exe」 — 임시 폴더에서 실행)는 숨기지 않는다
        d = re.sub(r"^\[[^\]]*\]\s*", "", _drop_orders(d)).strip()
        parts = [p.strip() for p in d.split(" — ") if p.strip()]
        if len(parts) >= 2:
            target = parts[0].strip("「」")
            return report_style.Item(mark, target, "·".join(_noun(p) for p in parts[1:3]))
        break
    summary = _noun(_first_sentence(r["summary"])) if r.get("summary") else ""
    if r["name"] == "check_ransomware_readiness" and not summary:
        summary = "대비 상태에 살펴볼 항목 있음"
    status = summary or ("위험 항목 있음" if mark == "🚨" else "확인 필요")
    reason = "일부 확인 못 함" if r.get("unknown") else ""
    return report_style.Item(mark, r["label"], status, reason)


def _items_for(r, mark):
    """점검 하나의 보고서 항목 — 영역 모듈(extract_items)이 알아보면 그 한 줄 항목들, 아니면 보수적인 한 줄."""
    got = report_style.extract_items(r["raw"]) if r.get("raw") else None
    if got:
        return list(got)
    return [_fallback_item(r, mark)]


def _build_report(spec, results, problems, notes, oks, unknowns, checked, total, offers, absorbed_inner):
    sid = next((k for k, v in SYMPTOMS.items() if v is spec), "")
    title = REPORT_TITLES.get(sid, f"{spec['title']} 점검")
    items = []
    for r in problems:
        items += _items_for(r, "🚨" if r["critical"] else "⚠️")
    for r in notes:
        got = report_style.extract_items(r["raw"]) if r.get("raw") else None
        items += list(got) if got else [_fallback_item({**r, "summary": r["summary"]}, "ℹ️")]
    for r in unknowns:
        items += _items_for(r, "❔")
    items = report_style.merge_items(items)
    items.sort(key=lambda it: report_style._ORDER.get(it.mark, 9))
    flagged = {" ".join(it.name.split()).lower() for it in items}
    ok_names = []
    for r in list(oks) + list(problems):
        names = report_style.extract_ok_names(r["raw"]) if r.get("raw") else None
        names = [n for n in (names or []) if not _JUNK_OK_NAME.search(n)]
        if r in oks and not names:
            names = [_ok_text(r)]
        for n in names:
            if " ".join(n.split()).lower() not in flagged and n not in ok_names:
                ok_names.append(n)
    if len(items) <= _MAX_ITEMS:
        shown = items
    else:      # 줄일 때는 위험 > 주의 > 확인 못 함 > 참고 순으로 남긴다(확인 못 한 것이 '외'로 숨지 않게)
        sev = {"🚨": 0, "⚠️": 1, "❔": 2, "ℹ️": 3}
        shown = sorted(items, key=lambda it: sev.get(it.mark, 9))[:5]
        items = shown + [it for it in items if it not in shown]
    body = report_style.render_report(title, shown, ok_names=ok_names).split("\n")
    body[0] = f"{report_style.TITLE_MARK} {title}{report_style.header_counts(items)} ({len(checked)}/{total}개 확인)"
    if len(items) > len(shown):
        rest = items[len(shown):]
        body.insert(1 + len(shown), f"ℹ️ 외 {len(rest)}건 — " + "·".join(it.name for it in rest))
    info_parts = []
    for r in results:
        if r["status"] != "info":
            continue
        if r["name"] == "get_top_cpu_processes":
            top = _top_cpu(r["raw"])
            if top:
                info_parts.append(f"CPU 1위 {top[0]} {top[1]:g}%")
        elif r["name"] == "get_system_info":
            ram, free = _ram_percent(r["raw"]), _disk_free_percent(r["raw"])
            if ram is not None:
                info_parts.append(f"메모리 {ram:.0f}% 사용")
            if free is not None:
                info_parts.append(f"디스크 여유 {free:.0f}%")
    if info_parts:
        body.append("ℹ️ 참고 — " + "·".join(info_parts))
    known = {v[1] for v in CHECK_INFO.values()}
    causes = [(t, w) for t, w in _causes([r for r in results if not r.get("absorbed")])
              if t not in known and not t.endswith("나왔어요")]
    for t, w in causes[:2]:
        body.append(_cause_note(t, w))
    if not items and not problems and not notes and oks and not unknowns:
        body.append("ℹ️ 범위 — 확인한 항목 기준, 제가 보지 않는 부분은 알 수 없음")
    text = "\n".join(body)
    if any(it.mark == "❔" for it in items):
        text += "\n(확인 못 한 것은 문제가 없다는 뜻이 아니에요)"
    closing = _closing(results, problems, offers)
    return f"{text}\n\n{closing}" if closing else text


def _closing(results, problems, offers) -> str:
    """맺음말 — 비서의 대화체 한 줄(제안이 없으면 생략)."""
    if not offers:
        return ""
    what = "더 자세히 보여 드릴까요?" if problems else "다시 읽어 볼까요?"
    label_of = {r["name"]: r["label"] for r in results}
    shown = [label_of[o.func] for o in offers]
    if len(offers) == 1:
        return (f"👉 제가 {shown[0]} 점검 결과를 {what}" if problems else f"👉 확인하지 못한 {shown[0]} 점검을 {what}")
    if all_read_only(o.func for o in offers):      # "응" 한 번에 전부 보여 주므로 번호 없이 한 번에 묻는다
        names = join_names(shown)
        return (f"👉 {names}{obj_josa(shown[-1])} 모두 {what}" if problems
                else f"👉 확인하지 못한 {names} 점검을 모두 {what}")
    # 읽기 전용이 아닌 것이 섞이면 번호로 나열하지 않고 가장 먼저 볼 것 하나만 묻는다(offers도 첫 항목 하나)
    return (f"👉 제가 {shown[0]} 점검 결과를 {what}" if problems else f"👉 확인하지 못한 {shown[0]} 점검을 {what}")


def run_triage_with_offers(symptom_id, func_map, progress=None, should_stop=None):
    """(답변 문장, 제안 Offer 목록)을 돌려준다. 문장 규칙은 run_triage와 같다 — 점검 도구가 없으면 ("", []),
    중단되면 (None, []). 이 함수 자체는 아무것도 등록하지 않는다(등록은 호출한 쪽이 한다).
    증상 id에 맞는 점검들을 돌려 답변 문장을 만든다. 점검 도구가 하나도 없으면 ""(호출한 쪽이 안내 문구를 쓴다).
    조치는 하지 않는다.
    progress(번호, 전체, 이름): 점검 하나를 시작하기 전에 부른다(진행 문구용). should_stop(): True면 중단하고
    None을 돌려준다(결과를 내보내지 않는다). 둘 다 선택이고, 콜백 오류는 점검을 멈추지 않는다."""
    spec = SYMPTOMS.get(symptom_id)
    if not spec or not isinstance(func_map, dict):
        return "", []
    names = list(spec["checks"]) + [d for d in spec.get("deferred_checks", ()) if d in func_map]
    if not any(n in func_map for n in names):
        return "", []
    results = []
    done = {}    # 이미 돌린 점검 결과 — check_ransomware_readiness가 같은 점검을 다시 돌리지 않게 넘긴다
    for i, n in enumerate(names, 1):
        if _safe_call(should_stop):
            return None, []
        _safe_call(progress, i, len(names), CHECK_INFO.get(n, (n,))[0])
        r = _run_one(n, func_map, reuse=done if n == "check_ransomware_readiness" else None)
        results.append(r)
        if r.get("obj") is not None and n in _REUSABLE:
            done[n] = r["obj"]
    if _safe_call(should_stop):
        return None, []

    # (D) 다른 점검이 안에 품은 점검의 '걸림'은 품은 쪽 한 줄에서만 말한다(품은 쪽도 걸렸을 때만 — 못 읽었으면 단독으로 보여 준다)
    status_of = {r["name"]: r["status"] for r in results}
    hidden = set()
    for absorber, inner in spec.get("absorbed_by", {}).items():
        if status_of.get(absorber) == "problem":
            hidden.update(inner)
    for r in results:
        r["absorbed"] = r["name"] in hidden and r["status"] == "problem"
    verdict = [r for r in results if r["status"] != "info"]
    checked = [r for r in verdict if r["status"] in ("ok", "problem")]
    problems = [r for r in results if r["status"] == "problem" and not r["absorbed"]]
    oks = [r for r in results if r["status"] == "ok" and not r.get("note")]
    notes = [r for r in results if r["status"] == "ok" and r.get("note")]
    unknowns = [r for r in results if r["status"] == "unknown"]
    total = len(verdict)

    offers = triage_offers([r for r in results if not r.get("absorbed")], func_map)
    if len(offers) > 1 and not all_read_only(o.func for o in offers):
        offers = offers[:1]      # 맺음말이 하나만 묻는 경우(위 _closing)와 맞춘다
    return _build_report(spec, results, problems, notes, oks, unknowns, checked, total, offers, None), offers
