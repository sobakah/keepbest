"""Videos: identical copies and remuxes (needs ffmpeg; skipped without it)."""

import subprocess

import pytest

from conftest import asked, make_video, remux, requires_ffmpeg, summary

pytestmark = requires_ffmpeg
WIDE = {"COLUMNS": "160"}      # room for the optional Streams column


def encoders() -> str:
    return subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout


# H.264 takes the "picture data only" path; MPEG-4 (DivX/Xvid) is available in every
# ffmpeg build. Each codec is tested with the containers it is found in.
# (MPEG-4 inside MPEG-TS can't be read by ffmpeg; such files are left alone.)
CASES = [
    pytest.param("libx264", ["mkv", "ts", "avi"],
                 marks=pytest.mark.skipif(" libx264 " not in encoders(), reason="no H.264 encoder")),
    pytest.param("mpeg4", ["mkv", "avi"]),
]


@pytest.mark.parametrize("codec, containers", CASES)
def test_remuxes_are_found_and_re_encodes_are_not(library, run_cli, codec, containers):
    clip = make_video(library / "clip.mp4", codec=codec)
    for ext in containers:
        remux(clip, library / f"clip.{ext}")
    make_video(library / "other.mp4", codec=codec, size="320x180")          # a different video
    counts = summary(run_cli(library.path, "--auto", "--dry-run"))
    assert (counts["scanned"], counts["remux"], counts["exact"]) == (len(containers) + 2, len(containers), 0)


def test_mpeg4_in_mpeg_ts_is_left_alone(library, run_cli):
    clip = make_video(library / "clip.mp4", codec="mpeg4")
    remux(clip, library / "clip.ts")              # ffmpeg can't read its picture size: not comparable
    before = library.files()
    counts = summary(run_cli(library.path, "--auto"))
    assert (counts["remux"], counts["failed"]) == (0, 0)
    assert library.files() == before


def test_identical_video_copies_are_exact_copies(library, run_cli):
    make_video(library / "clip.mp4", codec="mpeg4")
    library.copy_of("clip.mp4", "backup/clip copy.mp4")
    counts = summary(run_cli(library.path, "-r", "--auto", "--dry-run"))
    assert (counts["exact"], counts["remux"]) == (1, 0)


def test_when_tracks_differ_the_copy_with_audio_is_kept(library, run_cli):
    clip = make_video(library / "clip.mp4", codec="mpeg4")
    remux(clip, library / "silent.mkv", "-an")                             # the same video without audio
    result = run_cli(library.path, "--auto", "--dry-run")
    assert "Kept         clip.mp4" in result.stdout
    assert "Would delete silent.mkv" in result.stdout


def test_when_tracks_differ_the_user_is_asked(library, run_cli):
    clip = make_video(library / "clip.mp4", codec="mpeg4")
    remux(clip, library / "silent.mkv", "-an")
    result = run_cli(library.path, "--dry-run", keys=["", "s", "q"])     # start, skip the question, quit
    assert [q["group"]["tracks"] for q in asked(result, "RemuxScreen")] == ["covered"]
    assert "Skipped - all files kept." in result.stdout


def test_without_ffmpeg_remuxes_are_skipped_with_a_notice(library, run_cli, tmp_path):
    clip = make_video(library / "clip.mp4", codec="mpeg4")
    remux(clip, library / "clip.mkv")
    library.copy_of("clip.mp4", "clip copy.mp4")
    empty = tmp_path / "empty-path"
    empty.mkdir()
    result = run_cli(library.path, "--auto", "--dry-run", env={"PATH": str(empty)})
    assert "remuxed videos can't be detected" in result.stdout
    assert summary(result)["exact"] == 1


def test_no_videos_leaves_them_out(library, run_cli):
    make_video(library / "clip.mp4", codec="mpeg4")
    library.copy_of("clip.mp4", "clip copy.mp4")
    assert summary(run_cli(library.path, "--auto", "--dry-run", "--no-videos"))["scanned"] == 0


def with_other_sound(source, target):
    """The same video stream with another sound track (like a dub), also tagged 'und'."""
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
                    "-f", "lavfi", "-i", "sine=frequency=880:duration=3", "-map", "0:v", "-map", "1:a",
                    "-c:v", "copy", "-c:a", "aac", "-shortest", str(target)], check=True)
    return target


def test_other_sound_is_never_removed_automatically(library, run_cli):
    clip = make_video(library / "clip.mp4", codec="mpeg4")
    with_other_sound(clip, library / "dub.mkv")
    before = library.files()
    result = run_cli(library.path, "--auto")
    assert "Left alone" in result.stdout
    assert summary(result)["remux"] == 0
    assert library.files() == before


def test_other_sound_is_asked_about(library, run_cli):
    clip = make_video(library / "clip.mp4", codec="mpeg4")
    with_other_sound(clip, library / "dub.mkv")
    result = run_cli(library.path, "--dry-run", keys=["", "s", "q"])     # start, skip the question, quit
    assert [q["group"]["tracks"] for q in asked(result, "RemuxScreen")] == ["differ"]   # no file has them all


def test_a_copy_with_extra_tracks_is_kept_automatically(library, run_cli, tmp_path):
    clip = make_video(library / "clip.mp4", codec="mpeg4")
    subtitles = tmp_path / "subs.srt"
    subtitles.write_text("1\n00:00:00,000 --> 00:00:02,000\nHallo\n")
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(clip), "-i", str(subtitles),
                    "-map", "0", "-map", "1", "-c", "copy", "-c:s", "srt", str(library / "clip subs.mkv")],
                   check=True)
    result = run_cli(library.path, "--auto", "--dry-run")
    assert "Kept         clip subs.mkv" in result.stdout      # has everything clip.mp4 has, and more
    assert summary(result)["remux"] == 1


def test_a_second_video_stream_is_never_lost(library, run_cli):
    """Another camera angle in the same file is a track of its own: the copy with
    both video streams is kept, the one with only the first is removed."""
    clip = make_video(library / "clip.mp4", codec="mpeg4")
    angle = make_video(library.path.parent / "angle.mp4", codec="mpeg4", audio=False, size="160x120")
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(clip), "-i", str(angle),
                    "-map", "0:v", "-map", "1:v", "-map", "0:a", "-c", "copy", str(library / "both.mkv")],
                   check=True)
    result = run_cli(library.path, "--auto", "--dry-run", env=WIDE)
    assert "Kept         both.mkv" in result.stdout and "+1 video" in result.stdout
    assert "Would delete clip.mp4" in result.stdout


def test_a_timecode_track_counts_as_a_track(library, run_cli):
    clip = make_video(library / "clip.mp4", codec="mpeg4")
    remux(clip, library / "clip tc.mp4", "-timecode", "01:00:00:00")      # adds an MP4 tmcd data track
    result = run_cli(library.path, "--auto", "--dry-run", env=WIDE)
    assert "Kept         clip tc.mp4" in result.stdout and "1 data (tmcd)" in result.stdout
    assert "Would delete clip.mp4" in result.stdout


def with_two_sounds(source, target, swapped=False):
    """*source*'s video with two sound tracks (440 and 880 Hz), optionally in the other order."""
    order = ["-map", "2:a", "-map", "1:a"] if swapped else ["-map", "1:a", "-map", "2:a"]
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                    "-f", "lavfi", "-i", "sine=frequency=880:duration=3", "-map", "0:v", *order,
                    "-c:v", "copy", "-c:a", "aac", "-shortest", str(target)], check=True)
    return target


@pytest.fixture
def reordered(library):
    """The same video and sound tracks; the copies start with another sound."""
    clip = make_video(library.path.parent / "source.mp4", codec="mpeg4", audio=False)
    first = with_two_sounds(clip, library / "a.mkv")
    remux(first, library / "b.mkv", "-map", "-0:a", "-map", "0:a:1", "-map", "0:a:0")
    return library


def test_tracks_in_another_order_are_left_alone_without_questions(reordered, run_cli):
    before = reordered.files()
    result = run_cli(reordered.path, "--auto")
    assert "same tracks in another order" in result.stdout
    assert summary(result)["skipped"] == 1 and reordered.files() == before


def test_tracks_in_another_order_are_asked_about(reordered, run_cli):
    result = run_cli(reordered.path, "--dry-run", keys=["", "s", ""])   # start, skip, quit
    assert [q["group"]["tracks"] for q in asked(result, "RemuxScreen")] == ["reordered"]
    assert summary(result)["skipped"] == 1


def test_durations_are_compared_alike_for_both_files():
    from keepbest.core_logic import remux_candidates
    metas = [{"codec": "h264", "width": 320, "height": 240, "duration": d, "path": n}
             for d, n in ((100.0, "a"), (101.005, "b"))]          # 1.005 s apart: within 1 % of 101 s
    assert [m["path"] for m in remux_candidates(metas)] == ["a", "b"]


@pytest.mark.parametrize("signal_name", ["SIGTERM", "SIGINT"])
def test_a_signal_stops_the_hashing_at_once(library, fake_command, config_file, tmp_path, signal_name):
    """ffmpeg reading a big file must not keep the program waiting, and is not retried."""
    import signal
    import sys
    import time
    from conftest import REPO
    clip = make_video(library / "clip.mp4", codec="mpeg4")
    remux(clip, library / "clip.mkv")
    started = tmp_path / "ffmpeg.started"
    fake = fake_command("ffmpeg", script=f"echo x >> '{started}'\nexec sleep 30")   # "a huge file"
    proc = subprocess.Popen([sys.executable, "-m", "keepbest", "-c", str(config_file.path), "--auto",
                             "--stages", "2", str(library.path)], cwd=REPO,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    deadline = time.time() + 20
    while time.time() < deadline and len(started.read_text().split() if started.exists() else []) < 2:
        time.sleep(0.05)                                            # until both ffmpeg are running
    sent = time.time()
    proc.send_signal(getattr(signal, signal_name))                  # to keepbest only, not its group
    proc.communicate(timeout=20)
    assert time.time() - sent < 5
    assert len(started.read_text().split()) == 2                    # no second, track-less pass
    assert fake.calls(count=0) == []                                # every ffmpeg was ended early


def test_a_crash_on_the_tracks_falls_back_to_the_video_stream(tmp_path, fake_command):
    """ffmpeg dying of a crash (not an interrupt) on an odd track: the second pass,
    without the other tracks, still hashes the picture data."""
    from keepbest import video
    fake_command("ffmpeg", script='case "$*" in *"0:a?"*) kill -SEGV $$;; esac\necho "0,v,SHA256=abc"')
    clip = tmp_path / "clip.mkv"
    clip.write_bytes(b"x")
    assert video.stream_hashes({"path": clip, "codec": "mpeg4", "audio": []}) == ("abc", None)


def test_a_remux_without_a_duration_in_its_header_is_found(library, run_cli):
    """Matroska written to a pipe (a stream recording) has no duration in its header."""
    clip = make_video(library / "clip.mp4", codec="mpeg4")
    with open(library / "recorded.mkv", "wb") as out:
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(clip), "-map", "0", "-c", "copy",
                        "-f", "matroska", "-"], stdout=out, check=True)
    counts = summary(run_cli(library.path, "--auto", "--dry-run"))
    assert counts["remux"] == 1
