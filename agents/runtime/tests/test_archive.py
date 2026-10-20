import sys
from datetime import UTC, datetime

from agent_runtime.archive import SessionArchive

# Stands in for `hermes sessions export <dir> ... --after <time>`: writes the sessions it "has"
# (one per line in SESSIONS) as Markdown files with Hermes' front matter, and records its arguments.
FAKE_HERMES = """
import os, pathlib, sys
here = pathlib.Path(__file__).parent
(here / "args").write_text(" ".join(sys.argv[1:]))
if (here / "fail").exists():
    sys.exit("database is locked")
out = pathlib.Path(sys.argv[3])
for session in (here / "SESSIONS").read_text().split():
    (out / f"{session}-title.md").write_text(f'---\\nsession_id: "{session}"\\n---\\n# Turn {session}\\n')
"""


class FakeS3:
    def __init__(self):
        self.objects = {}

    def put_object(self, Bucket, Key, Body, ContentType):
        self.objects[(Bucket, Key)] = Body.decode()


def make_archive(tmp_path, sessions):
    script = tmp_path / "hermes"
    script.write_text(f"#!{sys.executable}\n{FAKE_HERMES}")
    script.chmod(0o755)
    (tmp_path / "SESSIONS").write_text(" ".join(sessions))
    s3 = FakeS3()
    return SessionArchive(s3, "company-bucket", "ceo", command=str(script)), s3


def test_each_session_is_archived_once_under_its_order(tmp_path):
    archive, s3 = make_archive(tmp_path, ["20261016_210021_aaa"])
    started = datetime(2026, 10, 16, 21, 0, 21, tzinfo=UTC)

    assert archive.archive(started, "TIENDA-123") == 1
    assert s3.objects == {
        ("company-bucket", "sessions/archives/ceo/TIENDA-123/20261016_210021_aaa.md"):
            '---\nsession_id: "20261016_210021_aaa"\n---\n# Turn 20261016_210021_aaa\n'
    }
    # Hermes filters by minute, so the export starts a minute early.
    assert "--after 2026-10-16 20:59" in (tmp_path / "args").read_text()

    # The next turn's export still contains the first session: only the new one is uploaded.
    (tmp_path / "SESSIONS").write_text("20261016_210021_aaa 20261016_210130_bbb")
    assert archive.archive(started, "TIENDA-456") == 1
    assert ("company-bucket", "sessions/archives/ceo/TIENDA-456/20261016_210130_bbb.md") in s3.objects
    assert len(s3.objects) == 2


def test_order_ids_are_safe_s3_folders(tmp_path):
    archive, s3 = make_archive(tmp_path, ["s1"])
    archive.archive(datetime.now(UTC), "../../etc passwd")
    archive.archive(datetime.now(UTC), None)
    assert [key for _, key in s3.objects] == ["sessions/archives/ceo/______etc_passwd/s1.md"]


def test_a_failed_export_never_fails_the_turn(tmp_path):
    archive, s3 = make_archive(tmp_path, ["s1"])
    (tmp_path / "fail").write_text("")
    assert archive.archive(datetime.now(UTC), "TIENDA-1") == 0
    assert s3.objects == {}
