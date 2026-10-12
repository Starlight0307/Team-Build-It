"""마이페이지 아래쪽 루미 버전 표시."""
import os
import re

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from settings.version import APP_VERSION


def test_version_looks_like_release_tag():
    # GitHub Release 태그(v1.0.0)와 같은 모양이어야 한다
    assert re.fullmatch(r"\d+\.\d+\.\d+", APP_VERSION)


def test_mypage_shows_version(qapp):
    from widget.mypage_widget import MyPageWidget
    w = MyPageWidget()
    assert w.lbl_version.text() == f"Lumi 버전 {APP_VERSION}"
