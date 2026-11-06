"""카톡 보내기 실기 시험 (core/kakao_send.py). 받는 사람은 나와의 채팅 이름으로 쓴다.

    python scripts/kakao_send_check.py "내 이름"          # 보내지 않고 대화창과 입력칸만 확인
    python scripts/kakao_send_check.py "내 이름" --send   # 시험 메시지 한 건 전송

실패 내용과 함께 창 상태를 출력한다. 대화 내용은 출력하지 않는다.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import kakao_send  # noqa: E402

MESSAGE = "루미 시험 메시지"


def window_state() -> str:
    if not kakao_send.IS_WIN:
        return ""
    hwnd = kakao_send._win.foreground()
    box = kakao_send._win.find_input(hwnd)
    return (f"앞 창 카카오톡={kakao_send._win.is_kakao(hwnd)}, 제목 글자수={len(kakao_send._win.title(hwnd))}, "
            f"포커스 칸={kakao_send._win.focus_child_class()!r}, 입력칸 찾음={bool(box)}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    to, really_send = sys.argv[1], "--send" in sys.argv[2:]
    if really_send and input(f"'{to}'에게 \"{MESSAGE}\"를 보낼까요? (yes 입력): ").strip() != "yes":
        print("취소했어요.")
        return 1
    try:
        print(kakao_send.send(to, MESSAGE, press_enter=really_send))
    except kakao_send.KakaoSendError as e:
        print(f"중단: {e}")
        print(window_state())
        return 1
    print(window_state())
    return 0


if __name__ == "__main__":
    sys.exit(main())
