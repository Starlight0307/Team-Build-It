"""마이페이지 아래쪽 루미 버전 표시."""
import os
import re

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from settings import version
from settings.version import APP_VERSION


def test_version_looks_like_release_tag():
    # GitHub Release 태그(v1.0.0)와 같은 모양이어야 한다
    assert re.fullmatch(r"\d+\.\d+\.\d+", APP_VERSION)


def test_mypage_shows_version(qapp):
    from widget.mypage_widget import MyPageWidget
    w = MyPageWidget()
    assert w.lbl_version.text().startswith(f"Lumi 버전 {APP_VERSION}")


def test_installed_build_reads_update_state(tmp_path):
    f = tmp_path / "update.json"
    assert version.installed_build(str(f)) == ""                 # 설치 프로그램 없이 실행
    f.write_text('{"sha": "' + "0123abc" + "d" * 33 + '"}')
    assert version.installed_build(str(f)) == "0123abc"
    f.write_text('{"sha": "short"}')
    assert version.installed_build(str(f)) == ""
    f.write_text("not json")
    assert version.installed_build(str(f)) == ""
