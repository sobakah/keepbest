"""Video files: metadata via ffprobe and container-independent stream hashes via ffmpeg.

Only exact content is compared here (no visual matching). A remuxed copy (the
same video stream in another container, e.g. MP4 -> MKV -> MPEG-TS) is found by
hashing the video stream's compressed picture data, which remuxing does not change.
The other tracks (further video streams, audio, subtitles, data such as
timecode) are hashed as well, so that copies with the same picture but other
sound or an extra camera angle are never taken for identical.
"""

from __future__ import annotations

import json
import math
import shutil
import signal
import subprocess
import threading
from collections import Counter
from pathlib import Path

VIDEO_EXTENSIONS = {'.mp4', '.m4v', '.mkv', '.webm', '.mov', '.avi', '.wmv', '.flv',
                    '.mpg', '.mpeg', '.ts', '.m2ts', '.mts', '.3gp', '.ogv'}

# Containers store H.264/HEVC with different framing and may add access-unit
# delimiters, parameter sets or SEI messages when remuxing (MPEG-TS does). These
# filters convert to one framing and keep only the picture data, so all remuxes
# of one stream hash alike. Other codecs are hashed as they are.
_PICTURE_ONLY_FILTERS = {
    "h264": "h264_mp4toannexb,filter_units=remove_types=6|7|8|9",
    "hevc": "hevc_mp4toannexb,filter_units=remove_types=32|33|34|35|39|40",
}
# MPEG-TS stores AAC with ADTS headers, MP4 and MKV without; this filter removes
# them, so an AAC track hashes alike in every container. It accepts only AAC.
_AUDIO_FILTERS = {"aac": "aac_adtstoasc"}


# ffprobe/ffmpeg run in worker threads. On Ctrl+C or SIGTERM the main thread
# stops them (stop_all), instead of waiting until each has read its whole file.
_running: set[subprocess.Popen] = set()
_lock = threading.Lock()
_stopping = threading.Event()


class Stopped(Exception):
    """A tool was stopped by stop_all or a signal: no point in trying again."""


_INTERRUPTED = {255} | {-getattr(signal, name) for name in ("SIGINT", "SIGTERM", "SIGHUP") if hasattr(signal, name)}


def _run(argv: list[str], timeout: float) -> subprocess.CompletedProcess:
    """subprocess.run that stop_all can end. Raises Stopped, OSError or TimeoutExpired."""
    with _lock:  # checked under the lock, so nothing starts after stop_all
        if _stopping.is_set():
            raise Stopped
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        _running.add(proc)
    try:
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            raise
    finally:
        with _lock:
            _running.discard(proc)
    # Ended by stop_all, or by Ctrl+C / SIGTERM / SIGHUP: ffmpeg exits with 255 then, or
    # dies of the signal. Any other end (a crash on a damaged file) is a failure.
    if _stopping.is_set() or proc.returncode in _INTERRUPTED:
        raise Stopped
    return subprocess.CompletedProcess(argv, proc.returncode, out, err)


def stop_all() -> None:
    """End every running ffprobe/ffmpeg and start no new ones (until allow_runs)."""
    _stopping.set()
    with _lock:
        for proc in _running:
            try:
                proc.kill()
            except OSError:
                pass


def allow_runs() -> None:
    _stopping.clear()


def is_video(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_EXTENSIONS


def tools_available() -> bool:
    return shutil.which("ffprobe") is not None and shutil.which("ffmpeg") is not None


def probe(path: Path) -> dict | None:
    """Stream details read from the container headers (no decoding). None if unreadable."""
    try:
        result = _run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
                      timeout=120)
        data = json.loads(result.stdout or "{}")
        stat = path.stat()
    except (OSError, ValueError, subprocess.TimeoutExpired, Stopped):
        return None
    streams = data.get("streams") or []
    videos = [s for s in streams if s.get("codec_type") == "video"]
    # Real video streams; cover art is stored as a video stream too. The first one
    # is compared; further ones (a second camera angle, say) count as tracks.
    real = [i for i, s in enumerate(videos) if not (s.get("disposition") or {}).get("attached_pic")]
    if not real:
        return None
    video_index = real[0]
    video = videos[video_index]

    def lang(stream):
        return (stream.get("tags") or {}).get("language", "und")

    try:
        duration = float((data.get("format") or {}).get("duration") or video.get("duration") or 0)
    except ValueError:
        duration = 0.0
    if not (math.isfinite(duration) and duration > 0):  # the header doesn't say
        duration = _measured_duration(path, video_index, stat.st_size)
    return {
        "path": path,
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "duration": duration,
        "codec": video.get("codec_name", "?"),
        "video_index": video_index,           # among the video streams, for ffmpeg's -map 0:v:N
        "extra_videos": [[i, videos[i].get("codec_name", "?")] for i in real[1:]],
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "audio": [[s.get("codec_name", "?"), int(s.get("channels") or 0), lang(s)]
                  for s in streams if s.get("codec_type") == "audio"],
        "subtitles": [[s.get("codec_name", "?"), lang(s)]
                      for s in streams if s.get("codec_type") == "subtitle"],
        # Timecode, GPS or camera telemetry tracks (MP4 tmcd, GoPro gpmd, ...).
        "data": [_data_name(s) for s in streams if s.get("codec_type") == "data"],
    }


def _data_name(stream: dict) -> str:
    """'tmcd', 'gpmd', ...: ffprobe calls most data codecs 'unknown', the tag is clearer."""
    name = stream.get("codec_name") or "unknown"
    tag = stream.get("codec_tag_string") or ""
    return tag if name == "unknown" and tag.isalnum() else name


def _hash_streams(meta: dict, tracks: bool) -> dict | None:
    """Run ffmpeg's streamhash; returns {output stream index: (type, sha256)} or None."""
    path, codec = meta["path"], meta["codec"]
    argv = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-i", str(path),
            "-map", f"0:v:{meta.get('video_index', 0)}"]
    video_codecs = [codec]
    if tracks:
        for index, extra_codec in meta.get("extra_videos", []):
            argv += ["-map", f"0:v:{index}"]
            video_codecs.append(extra_codec)
        argv += ["-map", "0:a?", "-map", "0:s?", "-map", "0:d?"]
    argv += ["-c", "copy"]
    for number, video_codec in enumerate(video_codecs):  # output video streams, in -map order
        if video_codec in _PICTURE_ONLY_FILTERS:
            argv += [f"-bsf:v:{number}", _PICTURE_ONLY_FILTERS[video_codec]]
    if tracks:
        for number, (audio_codec, *_rest) in enumerate(meta["audio"]):
            if audio_codec in _AUDIO_FILTERS:
                argv += [f"-bsf:a:{number}", _AUDIO_FILTERS[audio_codec]]
    argv += ["-f", "streamhash", "-hash", "sha256", "-"]
    try:
        size = path.stat().st_size
        # Generous: 2 minutes plus 5 MB/s, so a 50 GB file on a slow network drive
        # still gets hours, while a hung ffmpeg doesn't block the scan forever.
        result = _run(argv, timeout=120 + size / (5 * 1024 * 1024))
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:  # includes ffmpeg crashing on a damaged file
        return None
    hashes = {}
    for line in result.stdout.splitlines():  # "0,v,SHA256=..." per stream
        parts = line.split(",", 2)
        if len(parts) == 3 and parts[2].startswith("SHA256="):
            hashes[parts[0]] = (parts[1], parts[2][len("SHA256="):].strip())
    return hashes


def _measured_duration(path: Path, video_index: int, size: int) -> float:
    """The video stream's length from its packets' timestamps, for a file whose header
    has none (Matroska written to a pipe, an interrupted recording). Reads the
    file but decodes nothing; 0.0 if that fails too."""
    try:
        result = _run(["ffprobe", "-v", "error", "-select_streams", f"v:{video_index}",
                       "-show_entries", "packet=pts_time,duration_time", "-of", "csv=p=0", str(path)],
                      timeout=120 + size / (5 * 1024 * 1024))
    except (OSError, subprocess.TimeoutExpired, Stopped):
        return 0.0
    end = 0.0
    for line in result.stdout.splitlines():  # "pts_time,duration_time" per packet
        start, _, length = line.partition(",")
        try:
            packet_end = float(start) + (float(length) if length.strip() not in ("", "N/A") else 0.0)
        except ValueError:
            continue
        if math.isfinite(packet_end):
            end = max(end, packet_end)
    return end


def stream_hashes(meta: dict) -> tuple[str, tuple | None] | None:
    """(hash of the video stream's picture data, hashes of the other tracks: further
    video streams, audio, subtitles and data such as timecode or telemetry), or
    None if the video stream can't be read.

    Reads the whole file but decodes nothing (-c copy). If the tracks can't be
    hashed (an unusual subtitle format, say), they are returned as None, which
    counts as "different from every other copy"."""
    try:
        return _stream_hashes(meta)
    except Stopped:  # interrupted: don't start the second, track-less pass
        return None


def _stream_hashes(meta: dict) -> tuple[str, tuple | None] | None:
    hashes = _hash_streams(meta, tracks=True)
    if hashes is not None and "0" in hashes:
        others = tuple(f"{kind}:{digest}" for index, (kind, digest) in sorted(hashes.items(), key=lambda h: int(h[0]))
                       if index != "0")
        return hashes["0"][1], others
    hashes = _hash_streams(meta, tracks=False)
    if hashes is not None and "0" in hashes:
        return hashes["0"][1], None
    return None


def tracks_identical(metas: list[dict]) -> bool:
    """All copies have the same other tracks (video, audio, subtitles, data), byte for byte."""
    tracks = [m.get("tracks") for m in metas]
    return all(t is not None for t in tracks) and len(set(tracks)) == 1


def _in_order(part: tuple, whole: tuple) -> bool:
    """*part*'s tracks all appear in *whole*, in the same order (gaps allowed)."""
    rest = iter(whole)
    return all(track in rest for track in part)


def covering_copy(metas: list[dict]) -> int | None:
    """Index of the first copy that contains every track of the others (compared
    by content) in the same order, so removing the others loses no track and no
    copy's default track; else None."""
    if any(m.get("tracks") is None for m in metas):
        return None
    for i, meta in enumerate(metas):
        if all(_in_order(other["tracks"], meta["tracks"]) for other in metas):
            return i
    return None


def tracks_reordered(metas: list[dict]) -> bool:
    """The copies have the same tracks, but not in the same order: which language
    or sound a player starts with differs."""
    tracks = [m.get("tracks") for m in metas]
    if any(t is None for t in tracks) or len(set(tracks)) == 1:
        return False
    return all(Counter(t) == Counter(tracks[0]) for t in tracks)


def _duration_text(seconds: float) -> str:
    seconds = int(round(seconds))
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def describe(meta: dict) -> str:
    """'H264 1280x720 · 0:30 · 2 audio (eng, deu) · 1 subtitle (eng)'; further video
    streams ('+1 video') and data tracks ('1 data (tmcd)') when there are any."""
    parts = [f"{meta['codec'].upper()} {meta['width']}x{meta['height']}", _duration_text(meta["duration"])]
    extra = meta.get("extra_videos", [])
    if extra:
        parts[0] += f" +{len(extra)} video"
    audio, subs = meta["audio"], meta["subtitles"]
    if audio:
        langs = ", ".join(a[2] for a in audio)
        parts.append(f"{len(audio)} audio ({langs})")
    else:
        parts.append("no audio")
    if subs:
        parts.append(f"{len(subs)} subtitle{'s' if len(subs) != 1 else ''} ({', '.join(s[1] for s in subs)})")
    data = meta.get("data", [])
    if data:
        parts.append(f"{len(data)} data ({', '.join(data)})")
    return " · ".join(parts)
