"""
email_auth.py
공통 이메일 인증 모듈
- 6자리 인증코드 생성
- 5분 만료 처리
- Gmail SMTP 발송
- 회원가입 / 아이디 찾기 / 비밀번호 재설정에서 공통 사용
"""

import random
import string
import smtplib
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

try:
    from email_config import SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD
except ImportError:
    raise ImportError("email_config.py 파일이 없습니다. email_config.py를 프로젝트 루트에 추가하세요.")

# 인증코드 저장소: { email: { "code": "123456", "expires_at": timestamp } }
_auth_store: dict = {}

CODE_EXPIRY_SECONDS = 300  # 5분


# ────────────────────────────────────────────
# 1. 인증코드 생성 및 저장
# ────────────────────────────────────────────
def generate_code(email: str) -> str:
    """6자리 숫자 인증코드 생성 후 저장"""
    code = "".join(random.choices(string.digits, k=6))
    _auth_store[email] = {
        "code": code,
        "expires_at": time.time() + CODE_EXPIRY_SECONDS,
    }
    return code


# ────────────────────────────────────────────
# 2. 인증코드 검증
# ────────────────────────────────────────────
def verify_code(email: str, input_code: str) -> tuple[bool, str]:
    """
    인증코드 검증
    Returns:
        (True, "ok")            — 성공
        (False, "not_found")    — 코드 없음 (발송 안 함)
        (False, "expired")      — 만료됨
        (False, "wrong")        — 코드 불일치
    """
    entry = _auth_store.get(email)
    if not entry:
        return False, "not_found"
    if time.time() > entry["expires_at"]:
        del _auth_store[email]
        return False, "expired"
    if entry["code"] != input_code.strip():
        return False, "wrong"
    del _auth_store[email]  # 사용 후 삭제
    return True, "ok"


# ────────────────────────────────────────────
# 3. 이메일 발송
# ────────────────────────────────────────────
def _build_html(purpose: str, code: str) -> str:
    """이메일 HTML 본문 생성"""
    purpose_map = {
        "signup":   "회원가입",
        "find_id":  "아이디 찾기",
        "find_pw":  "비밀번호 재설정",
    }
    label = purpose_map.get(purpose, "본인 인증")
    return f"""
    <div style="font-family:Arial,sans-serif;max-width:480px;margin:0 auto;
                border:1px solid #ddd;border-radius:12px;overflow:hidden;">
      <div style="background:#1a1a2e;padding:24px;text-align:center;">
        <h2 style="color:#a78bfa;margin:0;">루미 LUMI</h2>
      </div>
      <div style="padding:32px;background:#fff;">
        <p style="font-size:16px;color:#333;">{label} 인증코드입니다.</p>
        <div style="background:#f3f0ff;border-radius:8px;padding:20px;
                    text-align:center;margin:24px 0;">
          <span style="font-size:36px;font-weight:bold;
                       letter-spacing:8px;color:#7c3aed;">{code}</span>
        </div>
        <p style="font-size:13px;color:#888;">
          ⏱ 이 코드는 <b>5분</b> 후 만료됩니다.<br>
          본인이 요청하지 않은 경우 이 메일을 무시하세요.
        </p>
      </div>
      <div style="background:#f9f9f9;padding:12px;text-align:center;">
        <p style="font-size:11px;color:#aaa;margin:0;">
          Team Build-It · teambuildit.2026@gmail.com
        </p>
      </div>
    </div>
    """


def send_verification_email(to_email: str, purpose: str = "signup") -> tuple[bool, str]:
    """
    인증코드 생성 후 이메일 발송

    Args:
        to_email: 수신자 이메일
        purpose:  "signup" | "find_id" | "find_pw"

    Returns:
        (True, "sent")          — 발송 성공
        (False, 오류메시지)      — 발송 실패
    """
    code = generate_code(to_email)

    subject_map = {
        "signup":   "[루미] 회원가입 인증코드",
        "find_id":  "[루미] 아이디 찾기 인증코드",
        "find_pw":  "[루미] 비밀번호 재설정 인증코드",
    }
    subject = subject_map.get(purpose, "[루미] 인증코드")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"루미 LUMI <{SMTP_USER}>"
    msg["To"] = to_email
    msg.attach(MIMEText(_build_html(purpose, code), "html", "utf-8"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
            server.ehlo()
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_USER, to_email, msg.as_string())
        return True, "sent"
    except smtplib.SMTPAuthenticationError:
        return False, "Gmail 앱 비밀번호가 올바르지 않습니다."
    except smtplib.SMTPRecipientsRefused:
        return False, "수신자 이메일 주소가 올바르지 않습니다."
    except TimeoutError:
        return False, "SMTP 서버 연결 시간 초과. 네트워크를 확인하세요."
    except Exception as e:
        return False, f"이메일 발송 실패: {e}"


# ────────────────────────────────────────────
# 4. 간편 사용 함수 (위젯에서 직접 import해서 사용)
# ────────────────────────────────────────────
def request_code(email: str, purpose: str = "signup") -> tuple[bool, str]:
    """인증코드 요청 (이메일 발송)"""
    return send_verification_email(email, purpose)


def confirm_code(email: str, code: str) -> tuple[bool, str]:
    """인증코드 확인"""
    ok, reason = verify_code(email, code)
    messages = {
        "ok":        "인증이 완료되었습니다.",
        "not_found": "인증코드를 먼저 요청하세요.",
        "expired":   "인증코드가 만료되었습니다. 다시 요청해주세요.",
        "wrong":     "인증코드가 올바르지 않습니다.",
    }
    return ok, messages.get(reason, "알 수 없는 오류")
