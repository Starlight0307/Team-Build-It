"""마이페이지의 개인정보처리방침 링크."""
from widget import mypage_widget


def test_privacy_url_is_the_website_page():
    assert mypage_widget.PRIVACY_URL.startswith("https://") and mypage_widget.PRIVACY_URL.endswith("/privacy.html")
