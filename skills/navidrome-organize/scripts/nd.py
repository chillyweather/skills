#!/usr/bin/env python3
"""Tag and arrange one music album for Navidrome.

Navidrome reads tags, not file names, so tags come first. Files and folders are renamed for people and other players.
Needs mutagen and Pillow. On first run the script installs them into its own environment under ~/.cache.

Commands:
  config                        Show which keys and tools are set.
  scan DIR                      Inventory audio files, their tags, and everything else.
  split DIR [--execute]         Split a CUE image (one file plus .cue) into tracks, losslessly.
  identify DIR                  Rank MusicBrainz releases that match the files.
  mb release ID | mb search A B Look up MusicBrainz.
  propose DIR --release ID      Write a plan: new tags and new paths. Nothing changes.
  apply PLAN [--execute]        Check a plan, then carry it out. Dry run without --execute.
  cover DIR                     Pick the best square front cover, save cover.jpg, embed it.
  artist-image DIR              Save artist.jpg from fanart.tv in the artist folder.
  verify DIR                    Check tags, track list, cover, and layout.
  undo LOG                      Reverse an apply or cover run.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

CONFIG_FILE = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "navidrome-organize" / "config.env"
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "navidrome-organize"
VENV = CACHE_DIR / "venv"


def bootstrap():
    """Re-run this script inside a private venv that has mutagen and Pillow."""
    try:
        import mutagen  # noqa: F401
        import PIL  # noqa: F401
        return
    except ImportError:
        pass
    py = VENV / "bin" / "python"
    if os.environ.get("NDO_IN_VENV"):
        sys.exit("error: mutagen or Pillow is missing from the private environment. "
                 f"Delete {VENV} and run again.")
    if not py.exists():
        print(f"First run: installing mutagen and Pillow into {VENV} ...", file=sys.stderr)
        subprocess.run([sys.executable, "-m", "venv", str(VENV)], check=True)
        subprocess.run([str(py), "-m", "pip", "install", "--quiet", "mutagen", "Pillow"], check=True)
    os.execve(str(py), [str(py), __file__, *sys.argv[1:]], {**os.environ, "NDO_IN_VENV": "1"})


bootstrap()

import argparse  # noqa: E402
import base64  # noqa: E402
import datetime as dt  # noqa: E402
import difflib  # noqa: E402
import hashlib  # noqa: E402
import io  # noqa: E402
import json  # noqa: E402
import pickle  # noqa: E402
import re  # noqa: E402
import shutil  # noqa: E402
import time  # noqa: E402
import unicodedata  # noqa: E402
import urllib.error  # noqa: E402
import urllib.parse  # noqa: E402
import urllib.request  # noqa: E402
from collections import Counter  # noqa: E402

import mutagen  # noqa: E402
from mutagen.apev2 import APEValue, BINARY  # noqa: E402
from mutagen.flac import FLAC, Picture  # noqa: E402
from mutagen.id3 import (APIC, ID3, TALB, TCMP, TCON, TDOR, TDRC, TDRL, TIT2, TMED, TPE1, TPE2, TPOS,  # noqa: E402
                         TPUB, TRCK, TSO2, TSOP, TSRC, TSST, TXXX, UFID)
from mutagen.mp4 import MP4, MP4Cover, MP4FreeForm  # noqa: E402
from PIL import Image  # noqa: E402

TOOL_DIR = "_navidrome-organize"
AUDIO = {".flac", ".mp3", ".m4a", ".mp4", ".alac", ".ogg", ".oga", ".opus", ".aiff", ".aif", ".wav", ".wv", ".ape"}
IMAGES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
OS_JUNK = {".ds_store", "thumbs.db", "desktop.ini"}
VA_ID = "89ad4ac3-39f7-470e-963a-56509c546377"
UA = "navidrome-organize/1.0 ( https://github.com/chillyweather/skills )"


def load_config() -> dict:
    conf = {}
    if CONFIG_FILE.exists():
        for line in CONFIG_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                conf[key.strip()] = value.strip().strip("'\"")
    for key in ("ACOUSTID_API_KEY", "FANARTTV_API_KEY", "COVER_MAX", "EMBED_MAX", "EMBED_COVER"):
        if os.environ.get(key):
            conf[key] = os.environ[key]
    conf.setdefault("COVER_MAX", "1500")
    conf.setdefault("EMBED_MAX", "1000")
    conf.setdefault("EMBED_COVER", "1")
    return conf


CONF = load_config()


def die(msg: str):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


# ----------------------------------------------------------------------------- http

_last_mb = [0.0]


def http(url: str, *, data: bytes | None = None, headers=None, raw=False, retries=4):
    hdrs = {"User-Agent": UA, "Accept": "application/json", **(headers or {})}
    for attempt in range(retries):
        req = urllib.request.Request(url, data=data, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = resp.read()
                return body if raw else json.loads(body or b"null")
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and attempt < retries - 1:
                time.sleep(2 + attempt * 2)
                continue
            if e.code == 404:
                return None
            raise RuntimeError(f"HTTP {e.code} for {url.split('?')[0]}: {e.read().decode(errors='replace')[:200]}") \
                from None
        except urllib.error.URLError as e:
            if attempt < retries - 1:
                time.sleep(2)
                continue
            raise RuntimeError(f"network error for {url.split('?')[0]}: {e.reason}") from None


def mb(path: str, **params):
    """MusicBrainz asks for at most one request a second."""
    wait = 1.1 - (time.time() - _last_mb[0])
    if wait > 0:
        time.sleep(wait)
    _last_mb[0] = time.time()
    params["fmt"] = "json"
    return http(f"https://musicbrainz.org/ws/2/{path}?{urllib.parse.urlencode(params)}")


RELEASE_INC = "recordings+artist-credits+labels+release-groups+media+isrcs"


def mb_release(release_id: str) -> dict:
    r = mb(f"release/{release_id}", inc=RELEASE_INC)
    if not r:
        die(f"release {release_id} not found on MusicBrainz")
    return r


def lucene(s: str) -> str:
    return re.sub(r'([+\-&|!(){}\[\]^"~*?:\\/])', r"\\\1", s)


# ----------------------------------------------------------------------------- text

TRANSLIT = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                    ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s",
                     "t", "u", "f", "h", "ts", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"]))


def norm(s: str | None) -> str:
    s = unicodedata.normalize("NFKC", s or "").lower().replace("ё", "е")
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def translit(s: str) -> str:
    return "".join(TRANSLIT.get(c, c) for c in s)


def similarity(a: str | None, b: str | None) -> float:
    a, b = norm(a), norm(b)
    if not a or not b:
        return 0.0
    return max(difflib.SequenceMatcher(None, a, b).ratio(),
               difflib.SequenceMatcher(None, translit(a), translit(b)).ratio())


def safe_name(s: str, limit: int = 180) -> str:
    s = s.replace("/", "_").replace("\\", "_")
    s = re.sub(r":\s", " - ", s)
    s = re.sub(r'[<>:"|?*\x00-\x1f]', "_", s)
    s = re.sub(r"\s+", " ", s).strip().strip(".")
    while len(s.encode()) > limit:
        s = s[:-1]
    return s.strip() or "_"


def credit(ac: list) -> tuple[str, list[str], list[str], str]:
    """Display name, names, ids, and sort name from a MusicBrainz artist credit."""
    display = "".join(c["name"] + c.get("joinphrase", "") for c in ac)
    names = [c["name"] for c in ac]
    ids = [c["artist"]["id"] for c in ac]
    sort = "".join(c["artist"].get("sort-name", c["name"]) + c.get("joinphrase", "") for c in ac)
    return display, names, ids, sort


def year_of(date: str | None) -> str | None:
    return date[:4] if date and date[:4].isdigit() else None


# ----------------------------------------------------------------------------- reading files

def family(f) -> str:
    name = type(f).__name__
    if name in ("FLAC", "OggVorbis", "OggOpus", "OggFLAC", "OggSpeex"):
        return "vorbis"
    if name in ("MP3", "AIFF", "WAVE", "EasyMP3"):
        return "id3"
    if name in ("MP4", "EasyMP4"):
        return "mp4"
    if name in ("WavPack", "MonkeysAudio", "APEv2File"):
        return "ape"
    return "unknown"


def open_audio(path: Path):
    f = mutagen.File(str(path))
    if f is None:
        raise RuntimeError(f"cannot read {path.name}")
    return f


def first(v):
    if isinstance(v, (list, tuple)):
        v = v[0] if v else None
    return str(v) if v is not None else None


def read_basic(path: Path) -> dict:
    out = {"path": str(path)}
    try:
        f = open_audio(path)
    except Exception as e:  # noqa: BLE001
        return {**out, "error": str(e)}
    info = f.info
    out.update(duration=round(getattr(info, "length", 0) or 0, 1),
               bitrate=getattr(info, "bitrate", None),
               sample_rate=getattr(info, "sample_rate", None),
               bits=getattr(info, "bits_per_sample", None),
               format=type(f).__name__)
    fam = family(f)
    t = {}
    if fam == "id3":
        # Read frames directly: mutagen's easy mode covers MP3 only, not ID3 inside AIFF or WAV.
        tags = f.tags or {}
        frames = {"title": "TIT2", "artist": "TPE1", "album": "TALB", "albumartist": "TPE2", "tracknumber": "TRCK",
                  "discnumber": "TPOS", "date": "TDRC"}
        for key, fid in frames.items():
            if fid in tags and tags[fid].text:
                t[key] = str(tags[fid].text[0])
        for key, frame in tags.items():
            if key.lower() == "txxx:musicbrainz album id" and frame.text:
                t["musicbrainz_albumid"] = str(frame.text[0])
        out["tags"] = t
        out["has_picture"] = has_picture(f)
        return out
    try:
        e = mutagen.File(str(path), easy=True) if fam == "mp4" else f
        tags = e.tags or {}
        for key in ("title", "artist", "album", "albumartist", "tracknumber", "discnumber", "date",
                    "musicbrainz_albumid", "barcode"):
            alt = {"albumartist": ["albumartist", "album artist", "album_artist"],
                   "tracknumber": ["tracknumber", "track"], "discnumber": ["discnumber", "disc"],
                   "date": ["date", "year"]}.get(key, [key])
            for k in alt:
                try:
                    v = tags.get(k) if fam != "ape" else tags.get(k.title()) or tags.get(k)
                except (KeyError, ValueError):
                    v = None
                if v:
                    t[key] = first(v) if fam != "ape" else str(v)
                    break
    except Exception:  # noqa: BLE001
        pass
    out["tags"] = t
    out["has_picture"] = has_picture(f)
    return out


def has_picture(f) -> bool:
    fam = family(f)
    if fam == "vorbis":
        return bool(getattr(f, "pictures", None)) or bool(f.tags and f.tags.get("METADATA_BLOCK_PICTURE"))
    if fam == "id3":
        return bool(f.tags and f.tags.getall("APIC"))
    if fam == "mp4":
        return bool(f.tags and f.tags.get("covr"))
    if fam == "ape":
        return bool(f.tags and "Cover Art (Front)" in f.tags)
    return False


def num_pair(v: str | None) -> tuple[int | None, int | None]:
    if not v:
        return None, None
    m = re.match(r"\s*(\d+)(?:\s*/\s*(\d+))?", str(v))
    return (int(m.group(1)), int(m.group(2)) if m.group(2) else None) if m else (None, None)


def guess_from_path(rel: Path) -> dict:
    """Track number, side position, disc, and title from a file name."""
    stem = rel.stem
    g = {}
    m = re.match(r"^\s*(?:(\d)[-.](\d{1,3})|([A-Ha-h])(\d{1,2})|(\d{1,3}))(?=[\s._\-)\]]|$)[\s._\-)\]]*(.*)$", stem)
    if m:
        if m.group(1):
            g["disc"], g["track"] = int(m.group(1)), int(m.group(2))
        elif m.group(3):
            g["side"] = f"{m.group(3).upper()}{int(m.group(4))}"
        else:
            g["track"] = int(m.group(5))
        rest = m.group(6)
    else:
        rest = stem
    rest = re.sub(r"^.+? - (?=.)", "", rest) if rest.count(" - ") == 1 and not re.match(r"^\d", rest) else rest
    g["title"] = rest.strip(" -_.") or None
    for part in rel.parts[:-1]:
        d = re.search(r"(?i)\b(?:cd|disc|disk|диск)\s*(\d{1,2})\b", part)
        if d:
            g["disc"] = int(d.group(1))
    return g


def walk(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d != TOOL_DIR)
        for name in sorted(filenames):
            if name.startswith(".") or name.lower() in OS_JUNK:
                continue
            p = Path(dirpath) / name
            if not p.is_symlink():
                yield p


def scan(root: Path) -> dict:
    audio, images, other, cues = [], [], [], []
    for p in walk(root):
        rel = p.relative_to(root)
        ext = p.suffix.lower()
        if ext in AUDIO:
            b = read_basic(p)
            b["path"] = str(rel)
            b["guess"] = guess_from_path(rel)
            audio.append(b)
        elif ext in IMAGES:
            images.append(str(rel))
        elif ext == ".cue":
            cues.append(str(rel))
            other.append(str(rel))
        else:
            other.append(str(rel))
    warnings, images_to_split = [], []
    for cue in cues:
        try:
            sheet = parse_cue(root / cue)
        except (OSError, ValueError):
            continue
        for entry in sheet["files"]:
            if len(entry["tracks"]) > 1:
                src = find_cue_audio(root / cue, entry["name"])
                images_to_split.append({"cue": cue, "audio": str(src.relative_to(root)) if src else None,
                                        "tracks": len(entry["tracks"])})
                warnings.append(f"{cue}: one audio file holds {len(entry['tracks'])} tracks (a CUE image). "
                                f"Run: split")
    albums = Counter(a["tags"].get("album") for a in audio)
    if len([k for k in albums if k]) > 1:
        warnings.append(f"files carry {len(albums)} different album tags: "
                        + ", ".join(f"{k!r} ({n})" for k, n in albums.most_common(6)))
    dirs = Counter(str(Path(a["path"]).parent) for a in audio)
    return {"root": str(root), "audio": audio, "images": images, "other": other,
            "folders_with_audio": dict(dirs), "cue_images": images_to_split, "warnings": warnings}


# ----------------------------------------------------------------------------- CUE split

LOSSLESS = {".flac", ".wav", ".ape", ".wv", ".tta", ".aiff", ".aif", ".alac"}


def read_cue_text(path: Path, encoding: str | None = None) -> str:
    raw = path.read_bytes()
    if encoding:
        return raw.decode(encoding)
    for enc in ("utf-8-sig", "cp1251"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def cue_time(s: str) -> int:
    """mm:ss:ff to CD frames, 75 a second."""
    m, sec, fr = (int(x) for x in s.split(":"))
    return (m * 60 + sec) * 75 + fr


def parse_cue(path: Path, encoding: str | None = None) -> dict:
    sheet = {"performer": None, "title": None, "date": None, "genre": None, "catalog": None, "files": []}
    track = None

    def value(rest: str) -> str:
        rest = rest.strip()
        return rest[1:rest.rfind('"')] if rest.startswith('"') and rest.count('"') >= 2 else rest

    for line in read_cue_text(path, encoding).splitlines():
        parts = line.strip().split(None, 1)
        if not parts:
            continue
        cmd, rest = parts[0].upper(), parts[1] if len(parts) > 1 else ""
        if cmd == "FILE":
            name = re.match(r'\s*"(.+)"|\s*(\S+)', rest)
            sheet["files"].append({"name": name.group(1) or name.group(2), "tracks": []})
            track = None
        elif cmd == "TRACK":
            if not sheet["files"]:
                raise ValueError("TRACK before FILE")
            track = {"number": int(rest.split()[0]), "title": None, "performer": None, "isrc": None,
                     "index0": None, "index1": None, "audio": "AUDIO" in rest.upper()}
            sheet["files"][-1]["tracks"].append(track)
        elif cmd == "INDEX" and track is not None:
            n, t = rest.split()[:2]
            track["index1" if int(n) == 1 else "index0" if int(n) == 0 else "other"] = cue_time(t)
        elif cmd in ("TITLE", "PERFORMER"):
            (track if track is not None else sheet)[cmd.lower()] = value(rest)
        elif cmd == "ISRC" and track is not None:
            track["isrc"] = value(rest)
        elif cmd == "CATALOG":
            sheet["catalog"] = value(rest)
        elif cmd == "REM":
            sub = rest.split(None, 1)
            if len(sub) == 2 and sub[0].upper() in ("DATE", "GENRE"):
                sheet[sub[0].lower()] = value(sub[1])
    for f in sheet["files"]:
        f["tracks"] = [t for t in f["tracks"] if t["audio"] and t["index1"] is not None]
    return sheet


def find_cue_audio(cue: Path, name: str) -> Path | None:
    """The file a cue sheet names. Rips often name a .wav that was later compressed, or use another encoding."""
    folder = cue.parent
    exact = folder / name
    if exact.exists():
        return exact
    stem = Path(name).stem
    for p in folder.iterdir():
        if p.suffix.lower() in AUDIO and p.stem == stem:
            return p
    for p in folder.iterdir():
        if p.suffix.lower() in AUDIO and p.stem == cue.stem:
            return p
    audio = [p for p in folder.iterdir() if p.suffix.lower() in AUDIO]
    big = [p for p in audio if p.stat().st_size > 50_000_000]
    if len(big) == 1:
        return big[0]
    return audio[0] if len(audio) == 1 else None


def audio_stream(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
                          "stream=sample_rate,bits_per_raw_sample,bits_per_sample,sample_fmt,codec_name:"
                          "format=duration", "-of", "json", str(path)], capture_output=True, text=True)
    data = json.loads(out.stdout or "{}")
    st = (data.get("streams") or [{}])[0]
    bits = int(st.get("bits_per_raw_sample") or 0) or int(st.get("bits_per_sample") or 0) or 16
    return {"rate": int(st.get("sample_rate") or 44100), "bits": bits, "codec": st.get("codec_name"),
            "duration": float((data.get("format") or {}).get("duration") or 0)}


def pcm_hash(jobs: list[tuple[Path, int, int | None]]) -> str:
    """SHA-256 of decoded audio, as 32-bit samples, over several (file, start sample, end sample) pieces."""
    h = hashlib.sha256()
    for path, start, end in jobs:
        trim = f"atrim=start_sample={start}" + (f":end_sample={end}" if end is not None else "")
        proc = subprocess.Popen(["ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-map", "0:a:0", "-af", trim,
                                 "-f", "s32le", "-"], stdout=subprocess.PIPE)
        for chunk in iter(lambda: proc.stdout.read(1 << 20), b""):
            h.update(chunk)
        if proc.wait() != 0:
            raise RuntimeError(f"ffmpeg could not decode {path.name}")
    return h.hexdigest()


def split_jobs(root: Path, encoding: str | None) -> tuple[list[dict], list[str]]:
    jobs, warnings = [], []
    for cue in sorted(p for p in walk(root) if p.suffix.lower() == ".cue"):
        try:
            sheet = parse_cue(cue, encoding)
        except (ValueError, OSError) as e:
            warnings.append(f"{cue.relative_to(root)}: cannot read: {e}")
            continue
        for entry in sheet["files"]:
            tracks = entry["tracks"]
            if len(tracks) < 2:
                continue
            src = find_cue_audio(cue, entry["name"])
            if src is None:
                warnings.append(f"{cue.relative_to(root)}: audio file '{entry['name']}' not found")
                continue
            info = audio_stream(src)
            per_frame = info["rate"] / 75
            if tracks[0]["index1"] > 75 * 2:
                warnings.append(f"{src.name}: {tracks[0]['index1'] / 75:.1f} s of audio before track 1 (a hidden "
                                f"pregap) is not extracted. It stays in the original file in leftovers.")
            disc = guess_from_path(cue.relative_to(root)).get("disc")
            lossless = src.suffix.lower() in LOSSLESS or info["codec"] in ("flac", "alac", "ape", "wavpack", "tta") \
                or (info["codec"] or "").startswith("pcm_")
            ext = ".flac" if lossless else src.suffix.lower()
            outs = []
            for i, t in enumerate(tracks):
                start = round(t["index1"] * per_frame)
                end = round(tracks[i + 1]["index1"] * per_frame) if i + 1 < len(tracks) else None
                title = t["title"] or f"Track {t['number']}"
                out = cue.parent / (safe_name(f"{t['number']:02d} - {title}") + ext)
                length = ((end if end is not None else info["duration"] * info["rate"]) - start) / info["rate"]
                outs.append({"out": out, "start": start, "end": end, "length": round(length, 1), "tags": {
                    "title": title, "artist": t["performer"] or sheet["performer"], "album": sheet["title"],
                    "albumartist": sheet["performer"], "tracknumber": t["number"], "tracktotal": len(tracks),
                    "discnumber": disc, "date": sheet["date"], "genre": sheet["genre"], "isrc": t["isrc"],
                    "barcode": sheet["catalog"] if re.fullmatch(r"\d{12,14}", sheet["catalog"] or "") else None}})
            jobs.append({"cue": cue, "src": src, "info": info, "lossless": lossless, "tracks": outs})
    return jobs, warnings


def cmd_split(args):
    root = Path(args.dir).resolve()
    if not shutil.which("ffmpeg"):
        die("ffmpeg is missing. Install it with: brew install ffmpeg")
    jobs, warnings = split_jobs(root, args.encoding)
    if not jobs:
        print("No CUE image to split.")
    for w in warnings:
        print(f"warning: {w}")
    for job in jobs:
        i = job["info"]
        print(f"{job['src'].relative_to(root)}  ({i['codec']}, {i['bits']} bit, {i['rate']} Hz) + "
              f"{job['cue'].name}")
        for t in job["tracks"]:
            print(f"  {t['out'].name}  ({int(t['length'] // 60)}:{int(t['length'] % 60):02d})")
            if t["out"].exists():
                die(f"target exists: {t['out']}")
        if not job["lossless"]:
            print("  warning: lossy source. Tracks are cut without re-encoding, accurate to about 26 ms.")
    if not jobs or not args.execute:
        if jobs:
            print("\nDry run. Nothing changed. Add --execute to split. Check that the titles read correctly; "
                  "if not, pass --encoding (for example cp1251 or shift_jis).")
        return
    for job in jobs:
        src, info, created = job["src"], job["info"], []
        try:
            for t in job["tracks"]:
                tmp = t["out"].with_name(".part-" + t["out"].name)
                if job["lossless"]:
                    trim = f"atrim=start_sample={t['start']}" + (f":end_sample={t['end']}" if t["end"] else "")
                    fmt = ["-sample_fmt", "s16"] if info["bits"] <= 16 else ["-sample_fmt", "s32",
                                                                            "-bits_per_raw_sample", str(info["bits"])]
                    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-i", str(src), "-map", "0:a:0", "-af", trim,
                           "-c:a", "flac", "-compression_level", "8", *fmt, "-map_metadata", "-1", "-f", "flac",
                           str(tmp)]
                else:
                    rate = info["rate"]
                    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-ss", f"{t['start'] / rate:.6f}"]
                    if t["end"] is not None:
                        cmd += ["-to", f"{t['end'] / rate:.6f}"]
                    cmd += ["-i", str(src), "-map", "0:a:0", "-c", "copy", "-map_metadata", "-1", str(tmp)]
                subprocess.run(cmd, check=True)
                tmp.rename(t["out"])
                created.append(t["out"])
                write_tags(t["out"], {k: v for k, v in t["tags"].items() if v not in (None, "")})
            if job["lossless"]:
                first = job["tracks"][0]["start"]
                whole = pcm_hash([(src, first, None)])
                parts = pcm_hash([(t["out"], 0, None) for t in job["tracks"]])
                if whole != parts:
                    raise RuntimeError("the split tracks do not add up to the original audio")
        except (subprocess.CalledProcessError, RuntimeError, OSError) as e:
            for p in created:
                p.unlink(missing_ok=True)
            for p in job["cue"].parent.glob(".part-*"):
                p.unlink()
            die(f"split of {src.name} failed, its new files were removed, the original is untouched: {e}")
        td = tool_dir(root)
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        moves = []
        for p in (src, job["cue"]):
            dest = td / "leftovers" / p.relative_to(root)
            dest.parent.mkdir(parents=True, exist_ok=True)
            p.rename(dest)
            moves.append([str(p), str(dest)])
        log = {"kind": "split", "moves": moves, "created": [str(p) for p in created], "tags": None}
        log_path = td / f"undo-{stamp}-split.json"
        log_path.write_text(json.dumps(log, indent=1, ensure_ascii=False))
        check = "audio verified bit for bit" if job["lossless"] else "lossy, not verified"
        print(f"\nsplit {src.name} into {len(created)} tracks ({check}).\n"
              f"original moved to {TOOL_DIR}/leftovers/. Delete it there once you are happy.\nundo log: {log_path}")


# ----------------------------------------------------------------------------- matching

def release_tracks(rel: dict) -> list[dict]:
    out = []
    for medium in rel.get("media", []):
        for t in medium.get("tracks", []) or []:
            rec = t.get("recording") or {}
            out.append({
                "disc": medium.get("position", 1),
                "position": t.get("position"),
                "number": t.get("number"),
                "title": t.get("title") or rec.get("title"),
                "length": (t.get("length") or rec.get("length") or 0) / 1000,
                "recording_id": rec.get("id"),
                "track_id": t.get("id"),
                "artist_credit": t.get("artist-credit") or rec.get("artist-credit") or rel.get("artist-credit"),
                "isrcs": rec.get("isrcs") or [],
                "disc_title": medium.get("title") or None,
                "format": medium.get("format"),
            })
    return out


def file_facts(a: dict) -> dict:
    t, g = a.get("tags", {}), a.get("guess", {})
    track, _ = num_pair(t.get("tracknumber"))
    disc, _ = num_pair(t.get("discnumber"))
    return {"title": t.get("title") or g.get("title"), "track": track or g.get("track"),
            "disc": disc or g.get("disc"), "side": g.get("side"), "duration": a.get("duration") or 0}


def match(files: list[dict], tracks: list[dict]) -> tuple[list, float]:
    """Greedy best pairs by title, duration, and number. Returns [(file_index, track_index, detail)], score."""
    multi_disc = len({t["disc"] for t in tracks}) > 1
    pairs = []
    for i, a in enumerate(files):
        f = file_facts(a)
        for j, t in enumerate(tracks):
            title = similarity(f["title"], t["title"]) if f["title"] else 0.5
            if f["duration"] and t["length"]:
                diff = abs(f["duration"] - t["length"])
                dur = max(0.0, 1 - diff / 20)
            else:
                diff, dur = None, 0.5
            num = 0.0
            if f["side"] and t["number"] and f["side"].upper() == str(t["number"]).upper():
                num = 1.0
            elif f["track"] == t["position"] and (f["disc"] or 1) == t["disc"]:
                num = 1.0
            elif f["track"] == t["position"] and not multi_disc:
                num = 1.0
            score = 0.4 * title + 0.35 * dur + 0.25 * num
            pairs.append((score, i, j, {"title_score": round(title, 2),
                                        "duration_diff": round(diff, 1) if diff is not None else None}))
    pairs.sort(key=lambda p: -p[0])
    used_f, used_t, out = set(), set(), []
    for score, i, j, detail in pairs:
        if i in used_f or j in used_t or score < 0.35:
            continue
        used_f.add(i)
        used_t.add(j)
        out.append((i, j, {**detail, "score": round(score, 2)}))
    if not out:
        return [], 0.0
    mean = sum(d["score"] for _, _, d in out) / len(out)
    return out, round(mean * len(out) / max(len(files), len(tracks)), 3)


def release_line(rel: dict) -> dict:
    display, *_ = credit(rel.get("artist-credit", []))
    formats = Counter(m.get("format") or "?" for m in rel.get("media", []))
    labels = rel.get("label-info") or []
    return {
        "id": rel["id"], "title": rel.get("title"), "artist": display, "date": rel.get("date"),
        "country": rel.get("country"), "status": rel.get("status"),
        "formats": " + ".join(f"{n}×{k}" if n > 1 else k for k, n in formats.items()),
        "tracks": sum(m.get("track-count", 0) for m in rel.get("media", [])),
        "label": ", ".join(filter(None, ((li.get("label") or {}).get("name") for li in labels))) or None,
        "catno": ", ".join(filter(None, (li.get("catalog-number") for li in labels))) or None,
        "barcode": rel.get("barcode") or None,
        "disambiguation": rel.get("disambiguation") or None,
        "cover_art": (rel.get("cover-art-archive") or {}).get("front"),
    }


# ----------------------------------------------------------------------------- identify

def fingerprint_releases(root: Path, audio: list[dict], limit: int = 8) -> Counter:
    key = CONF.get("ACOUSTID_API_KEY")
    if not key:
        die(f"no ACOUSTID_API_KEY in {CONFIG_FILE}")
    if not shutil.which("fpcalc"):
        die("fpcalc is missing. Install it with: brew install chromaprint")
    votes = Counter()
    for a in audio[:limit]:
        out = subprocess.run(["fpcalc", "-json", str(root / a["path"])], capture_output=True, text=True)
        if out.returncode != 0:
            continue
        fp = json.loads(out.stdout)
        data = urllib.parse.urlencode({"client": key, "meta": "recordings releaseids", "format": "json",
                                       "duration": int(fp["duration"]), "fingerprint": fp["fingerprint"]}).encode()
        r = http("https://api.acoustid.org/v2/lookup", data=data,
                 headers={"Content-Type": "application/x-www-form-urlencoded"})
        time.sleep(0.4)
        seen = set()
        for res in (r or {}).get("results", []):
            if res.get("score", 0) < 0.7:
                continue
            for rec in res.get("recordings", []) or []:
                for rel in rec.get("releases", []) or []:
                    seen.add(rel["id"])
        votes.update(seen)
    return votes


def cmd_identify(args):
    root = Path(args.dir).resolve()
    inv = scan(root)
    audio = [a for a in inv["audio"] if "error" not in a]
    if not audio:
        die("no readable audio files")
    for w in inv["warnings"]:
        print(f"warning: {w}")
    ids = Counter(a["tags"].get("musicbrainz_albumid") for a in audio if a["tags"].get("musicbrainz_albumid"))
    candidates = [i for i, _ in ids.most_common(2)]
    def most_common(values):
        values = [v for v in values if v]
        return Counter(values).most_common(1)[0][0] if values else None

    def clean(s):
        s = re.sub(r"[\[\(\{][^\]\)\}]*[\]\)\}]", " ", s or "")
        return re.sub(r"\s+", " ", s).strip(" -–") or None

    artist = args.artist or most_common(a["tags"].get("albumartist") or a["tags"].get("artist") for a in audio)
    album = args.album or most_common(a["tags"].get("album") for a in audio)
    parts = [p.strip() for p in re.split(r"\s+[-–]\s+", clean(root.name) or "") if p.strip()]
    folder_artist, folder_album = (parts[0], parts[-1]) if len(parts) >= 2 else (None, parts[0] if parts else None)
    # Tried in order until enough candidates turn up. Tags first, then the folder name, then looser forms.
    queries = []
    for a_, b_ in ((artist, album), (folder_artist or artist, folder_album), (None, album), (None, folder_album)):
        a_, b_ = clean(a_), clean(b_)
        if b_ and (a_, b_) not in queries:
            queries.append((a_, b_))
    barcode = most_common(a["tags"].get("barcode") for a in audio)
    if barcode:
        found = [r["id"] for r in (mb("release", query=f"barcode:{barcode}", limit=10) or {}).get("releases", [])]
        print(f"search barcode={barcode}: {len(found)} releases")
        candidates += found
    if args.fingerprint:
        votes = fingerprint_releases(root, audio)
        candidates += [i for i, _ in votes.most_common(args.limit)]
        print(f"fingerprints point to {len(votes)} releases")
    for a_, b_ in queries:
        if len(set(candidates)) >= args.limit or (barcode and candidates):
            break
        q = f'release:"{lucene(b_)}"' + (f' AND artist:"{lucene(a_)}"' if a_ else "")
        found = [r["id"] for r in (mb("release", query=q, limit=25) or {}).get("releases", [])]
        print(f"search artist={a_!r} album={b_!r}: {len(found)} releases")
        candidates += found
    seen, ranked = set(), []
    for rid in candidates:
        if rid in seen or len(seen) >= args.limit:
            continue
        seen.add(rid)
        rel = mb(f"release/{rid}", inc=RELEASE_INC)
        if not rel:
            continue
        pairs, score = match(audio, release_tracks(rel))
        diffs = [d["duration_diff"] for _, _, d in pairs if d["duration_diff"] is not None]
        ranked.append({**release_line(rel), "score": score, "matched": len(pairs),
                       "max_duration_diff": max(diffs) if diffs else None,
                       "from_tags": rid in ids})
    ranked.sort(key=lambda r: -r["score"])
    print(json.dumps(ranked, indent=1, ensure_ascii=False))


# ----------------------------------------------------------------------------- propose

def build_tags(rel: dict, rg: dict | None, t: dict, disc_total: int, track_total: int) -> dict:
    album_display, album_names, album_ids, album_sort = credit(rel.get("artist-credit", []))
    display, names, ids, sort = credit(t["artist_credit"] or rel.get("artist-credit", []))
    labels = rel.get("label-info") or []
    rgd = rel.get("release-group") or {}
    genres = []
    if rg:
        genres = [g["name"].title() for g in sorted(rg.get("genres", []), key=lambda g: -g.get("count", 0))
                  if g.get("count", 0) > 0][:3]
    tags = {
        "title": t["title"], "artist": display, "artists": names, "artistsort": sort,
        "album": rel.get("title"), "albumartist": album_display, "albumartists": album_names,
        "albumartistsort": album_sort,
        "tracknumber": t["position"], "tracktotal": track_total,
        "discnumber": t["disc"], "disctotal": disc_total, "discsubtitle": t["disc_title"],
        "date": rel.get("date"), "releasedate": rel.get("date"),
        "originaldate": rgd.get("first-release-date") or rel.get("date"),
        "label": [li["label"]["name"] for li in labels if li.get("label")] or None,
        "catalognumber": [li["catalog-number"] for li in labels if li.get("catalog-number")] or None,
        "barcode": rel.get("barcode") or None,
        "media": t["format"],
        "releasetype": [x.lower() for x in [rgd.get("primary-type")] + (rgd.get("secondary-types") or []) if x]
                       or None,
        "releasestatus": (rel.get("status") or "").lower() or None,
        "releasecountry": rel.get("country"),
        "script": (rel.get("text-representation") or {}).get("script"),
        "isrc": t["isrcs"] or None,
        "compilation": "1" if VA_ID in album_ids else None,
        "musicbrainz_trackid": t["recording_id"],
        "musicbrainz_releasetrackid": t["track_id"],
        "musicbrainz_albumid": rel["id"],
        "musicbrainz_releasegroupid": rgd.get("id"),
        "musicbrainz_artistid": ids,
        "musicbrainz_albumartistid": album_ids,
    }
    if genres:
        tags["genre"] = genres
    return {k: v for k, v in tags.items() if v not in (None, "", [])}


def propose(root: Path, release_id: str, dest: Path) -> dict:
    inv = scan(root)
    audio = [a for a in inv["audio"] if "error" not in a]
    rel = mb_release(release_id)
    rgid = (rel.get("release-group") or {}).get("id")
    rg = mb(f"release-group/{rgid}", inc="genres") if rgid else None
    tracks = release_tracks(rel)
    pairs, score = match(audio, tracks)
    warnings = list(inv["warnings"])
    warnings += [f"{a['path']}: {a['error']}" for a in inv["audio"] if "error" in a]

    album_display = credit(rel.get("artist-credit", []))[0]
    va = VA_ID in credit(rel.get("artist-credit", []))[2]
    year = year_of((rel.get("release-group") or {}).get("first-release-date")) or year_of(rel.get("date"))
    album_dir = dest / safe_name(album_display) / safe_name(f"{rel['title']} ({year})" if year else rel["title"])
    discs = sorted({t["disc"] for t in tracks})
    multi = len(discs) > 1
    per_disc = Counter(t["disc"] for t in tracks)

    files, matched_tracks = [], set()
    for i, j, detail in sorted(pairs, key=lambda p: (tracks[p[1]]["disc"], tracks[p[1]]["position"])):
        a, t = audio[i], tracks[j]
        matched_tracks.add(j)
        width = 3 if per_disc[t["disc"]] >= 100 else 2
        name = f"{t['position']:0{width}d} - " + (f"{credit(t['artist_credit'])[0]} - " if va else "") + t["title"]
        name = safe_name(name) + Path(a["path"]).suffix.lower()
        to = album_dir / (f"Disc {t['disc']}" if multi else "") / name
        files.append({"from": str(root / a["path"]), "to": str(to),
                      "tags": build_tags(rel, rg, t, len(discs), per_disc[t["disc"]]),
                      "old": a["tags"], "match": {"track": f"{t['disc']}-{t['position']}", **detail}})
        if detail["duration_diff"] is not None and detail["duration_diff"] > 10:
            warnings.append(f"{a['path']}: {detail['duration_diff']} s longer or shorter than track "
                            f"{t['disc']}-{t['position']} '{t['title']}'. Check the match.")
        elif a["tags"].get("title") and detail["title_score"] < 0.5:
            warnings.append(f"{a['path']}: title '{a['tags']['title']}' is far from '{t['title']}'. Check the match.")
    used = {audio[i]["path"] for i, _, _ in pairs}
    unmatched = [a["path"] for a in inv["audio"] if a["path"] not in used]
    missing = [f"{t['disc']}-{t['position']} {t['title']}" for j, t in enumerate(tracks) if j not in matched_tracks]
    if unmatched:
        warnings.append(f"{len(unmatched)} audio files match no track on this release and stay where they are.")
    if missing:
        warnings.append(f"{len(missing)} tracks of the release have no file: " + "; ".join(missing[:10]))

    leftovers = []
    scans_dir = album_dir / "Scans"
    for img in inv["images"]:
        leftovers.append({"from": str(root / img), "to": str(scans_dir / Path(img).name), "why": "image"})
    for o in inv["other"]:
        leftovers.append({"from": str(root / o), "to": str(album_dir / TOOL_DIR / "leftovers" / o), "why": "not audio"})
    old_tool = root / TOOL_DIR
    if old_tool.is_dir() and root != album_dir:
        for p in sorted(old_tool.rglob("*")):
            if p.is_file() and p.name != ".ndignore":
                leftovers.append({"from": str(p), "to": str(album_dir / TOOL_DIR / p.relative_to(old_tool)),
                                  "why": "earlier run"})
    names = Counter(Path(x["to"]).name.lower() for x in leftovers if x["why"] == "image")
    for x in leftovers:
        if x["why"] == "image" and names[Path(x["to"]).name.lower()] > 1:
            rel_parts = Path(x["from"]).relative_to(root).parts
            x["to"] = str(scans_dir / safe_name(" - ".join(rel_parts)))
    return {
        "source_dir": str(root), "album_dir": str(album_dir), "release": release_line(rel),
        "release_group_id": rgid, "match_score": score, "files": files, "extras": leftovers,
        "unmatched": unmatched, "missing_tracks": missing, "warnings": warnings,
    }


# ----------------------------------------------------------------------------- tag writing

VORBIS_KEYS = {
    "title": "TITLE", "artist": "ARTIST", "artists": "ARTISTS", "artistsort": "ARTISTSORT", "album": "ALBUM",
    "albumartist": "ALBUMARTIST", "albumartists": "ALBUMARTISTS", "albumartistsort": "ALBUMARTISTSORT",
    "tracknumber": "TRACKNUMBER", "tracktotal": "TRACKTOTAL", "discnumber": "DISCNUMBER", "disctotal": "DISCTOTAL",
    "discsubtitle": "DISCSUBTITLE", "date": "DATE", "releasedate": "RELEASEDATE", "originaldate": "ORIGINALDATE",
    "genre": "GENRE", "label": "LABEL", "catalognumber": "CATALOGNUMBER", "barcode": "BARCODE", "media": "MEDIA",
    "releasetype": "RELEASETYPE", "releasestatus": "RELEASESTATUS", "releasecountry": "RELEASECOUNTRY",
    "script": "SCRIPT", "isrc": "ISRC", "compilation": "COMPILATION",
    "musicbrainz_trackid": "MUSICBRAINZ_TRACKID", "musicbrainz_releasetrackid": "MUSICBRAINZ_RELEASETRACKID",
    "musicbrainz_albumid": "MUSICBRAINZ_ALBUMID", "musicbrainz_releasegroupid": "MUSICBRAINZ_RELEASEGROUPID",
    "musicbrainz_artistid": "MUSICBRAINZ_ARTISTID", "musicbrainz_albumartistid": "MUSICBRAINZ_ALBUMARTISTID",
}
# Old spellings that Navidrome also reads. They are removed so they cannot contradict the new tags.
VORBIS_STALE = ["ALBUM ARTIST", "ALBUM_ARTIST", "YEAR", "TOTALTRACKS", "TOTALDISCS", "TRACK", "DISC", "DISK",
                "ORIGINALYEAR", "ORIGYEAR", "MUSICBRAINZ_ALBUMTYPE", "MUSICBRAINZ_ALBUMSTATUS", "RELEASETYPE",
                "PUBLISHER", "ORGANIZATION"]
TXXX_DESC = {
    "artists": "ARTISTS", "albumartists": "ALBUMARTISTS", "catalognumber": "CATALOGNUMBER", "barcode": "BARCODE",
    "script": "SCRIPT", "releasetype": "MusicBrainz Album Type", "releasestatus": "MusicBrainz Album Status",
    "releasecountry": "MusicBrainz Album Release Country", "musicbrainz_releasetrackid": "MusicBrainz Release Track Id",
    "musicbrainz_albumid": "MusicBrainz Album Id", "musicbrainz_releasegroupid": "MusicBrainz Release Group Id",
    "musicbrainz_artistid": "MusicBrainz Artist Id", "musicbrainz_albumartistid": "MusicBrainz Album Artist Id",
}
TXXX_STALE = {"album artist", "albumartist", "musicbrainz_albumid", "musicbrainz_artistid", "originalyear",
              "musicbrainz_albumartistid", "musicbrainz_releasegroupid", "musicbrainz_releasetrackid"}
MP4_FREE = {
    "artists": "ARTISTS", "catalognumber": "CATALOGNUMBER", "barcode": "BARCODE", "label": "LABEL", "media": "MEDIA",
    "isrc": "ISRC", "script": "SCRIPT", "originaldate": "originaldate",
    "releasetype": "MusicBrainz Album Type", "releasestatus": "MusicBrainz Album Status",
    "releasecountry": "MusicBrainz Album Release Country", "musicbrainz_trackid": "MusicBrainz Track Id",
    "musicbrainz_releasetrackid": "MusicBrainz Release Track Id", "musicbrainz_albumid": "MusicBrainz Album Id",
    "musicbrainz_releasegroupid": "MusicBrainz Release Group Id", "musicbrainz_artistid": "MusicBrainz Artist Id",
    "musicbrainz_albumartistid": "MusicBrainz Album Artist Id",
}
APE_KEYS = {
    "title": "Title", "artist": "Artist", "artists": "Artists", "artistsort": "ArtistSort", "album": "Album",
    "albumartist": "Album Artist", "albumartists": "AlbumArtists", "albumartistsort": "AlbumArtistSort",
    "discsubtitle": "DiscSubtitle", "date": "Year", "releasedate": "ReleaseDate", "originaldate": "OriginalDate",
    "genre": "Genre", "label": "Label", "catalognumber": "CatalogNumber", "barcode": "Barcode", "media": "Media",
    "releasetype": "ReleaseType", "releasestatus": "ReleaseStatus", "releasecountry": "ReleaseCountry",
    "script": "Script", "isrc": "ISRC", "compilation": "Compilation",
    "musicbrainz_trackid": "MUSICBRAINZ_TRACKID", "musicbrainz_releasetrackid": "MUSICBRAINZ_RELEASETRACKID",
    "musicbrainz_albumid": "MUSICBRAINZ_ALBUMID", "musicbrainz_releasegroupid": "MUSICBRAINZ_RELEASEGROUPID",
    "musicbrainz_artistid": "MUSICBRAINZ_ARTISTID", "musicbrainz_albumartistid": "MUSICBRAINZ_ALBUMARTISTID",
}


def as_list(v) -> list[str]:
    return [str(x) for x in v] if isinstance(v, list) else [str(v)]


def write_tags(path: Path, tags: dict):
    f = open_audio(path)
    fam = family(f)
    if fam == "vorbis":
        if f.tags is None:
            f.add_tags()
        for key in list(VORBIS_KEYS.values()) + VORBIS_STALE:
            if key in f.tags:
                del f.tags[key]
        for k, v in tags.items():
            f.tags[VORBIS_KEYS[k]] = as_list(v)
        f.save()
    elif fam == "id3":
        if f.tags is None:
            f.add_tags()
        t = f.tags
        for fid in ("TIT2", "TPE1", "TSOP", "TALB", "TPE2", "TSO2", "TRCK", "TPOS", "TSST", "TDRC", "TDRL", "TDOR",
                    "TCON", "TPUB", "TMED", "TSRC", "TCMP", "TYER", "TDAT", "TORY"):
            t.delall(fid)
        t.delall("UFID:http://musicbrainz.org")
        managed = {d.lower() for d in TXXX_DESC.values()} | TXXX_STALE
        for key in list(t.keys()):
            if key.startswith("TXXX:") and key[5:].lower() in managed:
                del t[key]
        simple = {"title": TIT2, "artist": TPE1, "artistsort": TSOP, "album": TALB, "albumartist": TPE2,
                  "albumartistsort": TSO2, "discsubtitle": TSST, "date": TDRC, "releasedate": TDRL,
                  "originaldate": TDOR, "genre": TCON, "label": TPUB, "media": TMED, "isrc": TSRC,
                  "compilation": TCMP}
        for k, frame in simple.items():
            if k in tags:
                t.add(frame(encoding=3, text=as_list(tags[k])))
        if "tracknumber" in tags:
            t.add(TRCK(encoding=3, text=f"{tags['tracknumber']}/{tags.get('tracktotal', '')}".rstrip("/")))
        if "discnumber" in tags:
            t.add(TPOS(encoding=3, text=f"{tags['discnumber']}/{tags.get('disctotal', '')}".rstrip("/")))
        for k, desc in TXXX_DESC.items():
            if k in tags:
                t.add(TXXX(encoding=3, desc=desc, text=as_list(tags[k])))
        if "musicbrainz_trackid" in tags:
            t.add(UFID(owner="http://musicbrainz.org", data=tags["musicbrainz_trackid"].encode()))
        if type(f).__name__ == "MP3":
            f.save(v2_version=4, v1=0)
        else:
            f.save(v2_version=4)
    elif fam == "mp4":
        if f.tags is None:
            f.add_tags()
        t = f.tags
        for key in ("©nam", "©ART", "soar", "©alb", "aART", "soaa", "trkn", "disk", "©day", "©gen", "cpil"):
            t.pop(key, None)
        for desc in MP4_FREE.values():
            t.pop(f"----:com.apple.iTunes:{desc}", None)
        simple = {"title": "©nam", "artist": "©ART", "artistsort": "soar", "album": "©alb", "albumartist": "aART",
                  "albumartistsort": "soaa", "date": "©day", "genre": "©gen"}
        for k, key in simple.items():
            if k in tags:
                t[key] = as_list(tags[k])
        if "tracknumber" in tags:
            t["trkn"] = [(int(tags["tracknumber"]), int(tags.get("tracktotal", 0)))]
        if "discnumber" in tags:
            t["disk"] = [(int(tags["discnumber"]), int(tags.get("disctotal", 0)))]
        if tags.get("compilation") == "1":
            t["cpil"] = True
        for k, desc in MP4_FREE.items():
            if k in tags:
                t[f"----:com.apple.iTunes:{desc}"] = [MP4FreeForm(x.encode()) for x in as_list(tags[k])]
        f.save()
    elif fam == "ape":
        if f.tags is None:
            f.add_tags()
        t = f.tags
        for key in list(APE_KEYS.values()) + ["Track", "Disc", "Album_Artist", "AlbumArtist"]:
            if key in t:
                del t[key]
        for k, key in APE_KEYS.items():
            if k in tags:
                t[key] = as_list(tags[k])
        if "tracknumber" in tags:
            t["Track"] = f"{tags['tracknumber']}/{tags.get('tracktotal', '')}".rstrip("/")
        if "discnumber" in tags:
            t["Disc"] = f"{tags['discnumber']}/{tags.get('disctotal', '')}".rstrip("/")
        f.save()
    else:
        raise RuntimeError(f"cannot write tags to {path.name} ({type(f).__name__})")


def embed_cover(path: Path, jpeg: bytes, size: tuple[int, int]):
    f = open_audio(path)
    fam = family(f)
    if fam == "vorbis":
        pic = Picture()
        pic.type, pic.mime, pic.desc = 3, "image/jpeg", "Cover"
        pic.width, pic.height, pic.depth = size[0], size[1], 24
        pic.data = jpeg
        if isinstance(f, FLAC):
            f.clear_pictures()
            f.add_picture(pic)
        else:
            if f.tags is None:
                f.add_tags()
            for k in ("METADATA_BLOCK_PICTURE", "COVERART", "COVERARTMIME"):
                if k in f.tags:
                    del f.tags[k]
            f.tags["METADATA_BLOCK_PICTURE"] = [base64.b64encode(pic.write()).decode()]
        f.save()
    elif fam == "id3":
        if f.tags is None:
            f.add_tags()
        f.tags.delall("APIC")
        f.tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=jpeg))
        if type(f).__name__ == "MP3":
            f.save(v2_version=4, v1=0)
        else:
            f.save(v2_version=4)
    elif fam == "mp4":
        if f.tags is None:
            f.add_tags()
        f.tags["covr"] = [MP4Cover(jpeg, imageformat=MP4Cover.FORMAT_JPEG)]
        f.save()
    elif fam == "ape":
        if f.tags is None:
            f.add_tags()
        f.tags["Cover Art (Front)"] = APEValue(b"cover.jpg\x00" + jpeg, BINARY)
        f.save()


def snapshot(path: Path) -> dict:
    """Everything needed to put a file's tags back exactly."""
    f = open_audio(path)
    fam = family(f)
    if fam == "vorbis":
        return {"fam": fam, "tags": list(f.tags) if f.tags is not None else None,
                "pics": [p.write() for p in getattr(f, "pictures", [])]}
    if fam == "id3":
        if f.tags is None:
            return {"fam": fam, "id3": None}
        buf = io.BytesIO()
        f.tags.save(buf, v2_version=f.tags.version[1] if f.tags.version[1] in (3, 4) else 4)
        return {"fam": fam, "id3": buf.getvalue(), "version": f.tags.version[1]}
    if fam == "mp4":
        return {"fam": fam, "items": list(f.tags.items()) if f.tags is not None else None}
    if fam == "ape":
        return {"fam": fam, "items": [(k, v.kind, v.value if v.kind != BINARY else bytes(v.value))
                                      for k, v in f.tags.items()] if f.tags is not None else None}
    raise RuntimeError(f"cannot back up tags of {path.name}")


def restore(path: Path, snap: dict):
    f = open_audio(path)
    fam = snap["fam"]
    if fam == "vorbis":
        if snap["tags"] is None:
            f.delete()
            f = open_audio(path)
        else:
            if f.tags is None:
                f.add_tags()
            f.tags.clear()
            for k, v in snap["tags"]:
                f.tags.append((k, v))
        if isinstance(f, FLAC):
            f.clear_pictures()
            for data in snap["pics"]:
                f.add_picture(Picture(data))
        if f.tags is not None or isinstance(f, FLAC):
            f.save()
    elif fam == "id3":
        if snap["id3"] is None:
            f.delete()
            return
        # Copy frames into the file's own tag object, so ID3 inside AIFF and WAV chunks stays in its chunk.
        saved = ID3(io.BytesIO(snap["id3"]))
        if f.tags is None:
            f.add_tags()
        f.tags.clear()
        for frame in saved.values():
            f.tags.add(frame)
        version = snap.get("version") if snap.get("version") in (3, 4) else 4
        if type(f).__name__ == "MP3":
            f.save(v2_version=version, v1=0)
        else:
            f.save(v2_version=version)
    elif fam == "mp4":
        if snap["items"] is None:
            f.delete()
            return
        if f.tags is None:
            f.add_tags()
        f.tags.clear()
        for k, v in snap["items"]:
            f.tags[k] = v
        f.save()
    elif fam == "ape":
        if snap["items"] is None:
            f.delete()
            return
        if f.tags is None:
            f.add_tags()
        f.tags.clear()
        for k, kind, value in snap["items"]:
            f.tags[k] = APEValue(value.encode() if isinstance(value, str) else value, kind)
        f.save()


# ----------------------------------------------------------------------------- apply and undo

def check_plan(plan: dict) -> list[str]:
    errors = []
    src = Path(plan["source_dir"])
    if not src.is_dir():
        return [f"folder not found: {src}"]
    moves = plan["files"] + plan["extras"]
    froms = {str(Path(m["from"])).lower() for m in moves}
    seen = {}
    for m in moves:
        a, b = Path(m["from"]), Path(m["to"])
        if not a.exists():
            errors.append(f"missing: {a}")
        elif a.is_symlink():
            errors.append(f"symlink: {a}")
        key = str(b).lower()
        if key in seen:
            errors.append(f"two files go to {b}: {seen[key]} and {a}")
        seen[key] = str(a)
        if b.exists() and key not in froms:
            errors.append(f"target exists: {b}")
    return errors


def prune_empty(top: Path):
    """Remove empty folders under top, and top itself when it ends up empty."""
    if not top.exists():
        return
    for dirpath, _, _ in os.walk(top, topdown=False):
        d = Path(dirpath)
        if TOOL_DIR in d.parts:
            continue
        rest = [x for x in os.listdir(d) if x.lower() not in OS_JUNK]
        if not rest:
            for x in os.listdir(d):
                (d / x).unlink()
            d.rmdir()


def move_all(pairs: list[tuple[Path, Path]], stage: Path) -> list[tuple[Path, Path]]:
    """Move through a staging folder, so swaps and case-only renames work. Rolls back on failure."""
    stage.mkdir(parents=True, exist_ok=True)
    staged, done = [], []
    try:
        for i, (a, b) in enumerate(pairs):
            a.rename(stage / str(i))
            staged.append((i, a, b))
        for i, a, b in staged:
            b.parent.mkdir(parents=True, exist_ok=True)
            (stage / str(i)).rename(b)
            done.append((i, a, b))
    except OSError as e:
        for i, a, b in reversed(done):
            b.rename(a)
        for i, a, b in staged:
            if (stage / str(i)).exists():
                a.parent.mkdir(parents=True, exist_ok=True)
                (stage / str(i)).rename(a)
        shutil.rmtree(stage, ignore_errors=True)
        hint = " The folders are on different disks; use --dest on the same disk." if e.errno == 18 else ""
        raise RuntimeError(f"move failed, all files restored: {e}.{hint}") from None
    stage.rmdir()
    return [(a, b) for _, a, b in done]


def tool_dir(album_dir: Path) -> Path:
    d = album_dir / TOOL_DIR
    d.mkdir(parents=True, exist_ok=True)
    (d / ".ndignore").touch()
    return d


def show_plan(plan: dict):
    r = plan["release"]
    print(f"release: {r['artist']} - {r['title']} ({r['date']}, {r['country']}, {r['formats']}, {r['label']} "
          f"{r['catno'] or ''}) https://musicbrainz.org/release/{r['id']}")
    print(f"folder:  {plan['source_dir']}\n     -> {plan['album_dir']}")
    if plan["files"]:
        t = plan["files"][0]["tags"]
        for k in ("album", "albumartist", "date", "originaldate", "genre", "label", "catalognumber", "releasetype"):
            if k in t:
                print(f"  {k}: {t[k]}")
    print()
    for m in plan["files"]:
        old = m["old"]
        new = m["tags"]
        rel_to = Path(m["to"]).relative_to(plan["album_dir"])
        print(f"  {Path(m['from']).name}\n    -> {rel_to}   (match {m['match']['score']}, "
              f"{m['match']['duration_diff']} s)")
        for k in ("title", "artist"):
            if (old.get(k) or "") != str(new.get(k, "")):
                print(f"       {k}: {old.get(k)!r} -> {new.get(k)!r}")
    for x in plan["extras"]:
        print(f"  {Path(x['from']).relative_to(plan['source_dir'])} -> "
              f"{Path(x['to']).relative_to(plan['album_dir'])}")
    print(f"\n{len(plan['files'])} tracks, {len(plan['extras'])} other files, match score {plan['match_score']}.")
    for w in plan["warnings"]:
        print(f"warning: {w}")
    for u in plan["unmatched"]:
        print(f"unmatched, left in place: {u}")


def cmd_apply(args):
    plan = json.loads(Path(args.plan).read_text())
    show_plan(plan)
    errors = check_plan(plan)
    if errors:
        for e in errors:
            print(f"ERROR: {e}")
        die("plan has errors, nothing changed")
    if not args.execute:
        print("\nDry run. Nothing changed. Add --execute to apply.")
        return
    album_dir = Path(plan["album_dir"])
    src = Path(plan["source_dir"])
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backups = {m["to"]: snapshot(Path(m["from"])) for m in plan["files"]}
    pairs = [(Path(m["from"]), Path(m["to"])) for m in plan["files"] + plan["extras"]]
    common = Path(os.path.commonpath([str(src), str(album_dir)]))
    done = move_all([p for p in pairs if p[0] != p[1]], common / f".nd-staging-{os.getpid()}")
    written = []
    try:
        for m in plan["files"]:
            write_tags(Path(m["to"]), m["tags"])
            written.append(m["to"])
    except Exception as e:  # noqa: BLE001
        for path in written:
            restore(Path(path), backups[path])
        for a, b in reversed(done):
            a.parent.mkdir(parents=True, exist_ok=True)
            b.rename(a)
        for d in (album_dir, album_dir.parent):
            if d.exists() and d != src and not any(f for f in d.rglob("*") if f.is_file()):
                shutil.rmtree(d)
        die(f"tag writing failed, everything restored: {e}")
    td = tool_dir(album_dir)
    (td / f"tags-{stamp}.pickle").write_bytes(pickle.dumps(backups))
    log = {"kind": "apply", "moves": [[str(a), str(b)] for a, b in done], "tags": str(td / f"tags-{stamp}.pickle"),
           "created": []}
    log_path = td / f"undo-{stamp}.json"
    log_path.write_text(json.dumps(log, indent=1, ensure_ascii=False))
    if src != album_dir:
        old_tool = src / TOOL_DIR
        if old_tool.is_dir() and not any(p.is_file() and p.name != ".ndignore" for p in old_tool.rglob("*")):
            shutil.rmtree(old_tool)
        prune_empty(src)
    print(f"\nDone. {len(plan['files'])} tracks tagged, {len(done)} files moved.\nalbum: {album_dir}\n"
          f"undo log: {log_path}")


def cmd_undo(args):
    log_path = Path(args.log)
    log = json.loads(log_path.read_text())
    backups = pickle.loads(Path(log["tags"]).read_bytes()) if log.get("tags") else {}
    for path, snap in backups.items():
        if Path(path).exists():
            restore(Path(path), snap)
        else:
            print(f"skip tags, file moved since: {path}")
    leftovers = log_path.parent / "leftovers" / f"undone-{dt.datetime.now():%Y%m%d-%H%M%S}"
    for c in log.get("created", []):
        if log["kind"] == "split" and Path(c).exists():
            Path(c).unlink()
        elif Path(c).exists():
            leftovers.mkdir(parents=True, exist_ok=True)
            Path(c).rename(leftovers / Path(c).name)
    pairs = [(Path(b), Path(a)) for a, b in reversed(log["moves"]) if Path(b).exists() and not Path(a).exists()]
    if pairs:
        common = Path(os.path.commonpath([str(p) for pair in pairs for p in pair]))
        move_all(pairs, common / f".nd-staging-{os.getpid()}")
    log_path.rename(log_path.with_suffix(".undone.json"))
    album_dir = log_path.parent.parent
    if log["kind"] != "split":
        prune_empty(album_dir)
    # After undoing an apply, a folder that holds only this tool's own records is removed with its empty parent.
    if log["kind"] == "apply" and album_dir.exists() and all(
            TOOL_DIR in p.relative_to(album_dir).parts for p in album_dir.rglob("*") if p.is_file()):
        shutil.rmtree(album_dir)
        if album_dir.parent.exists() and not any(album_dir.parent.iterdir()):
            album_dir.parent.rmdir()
    print(f"Undone: {len(backups)} files' tags restored, {len(pairs)} files moved back.")


# ----------------------------------------------------------------------------- cover

FORMAT_RANK = [("vinyl", 0), ("cd", 1), ("sacd", 1), ("digital", 2), ("cassette", 9)]


def format_rank(rel: dict) -> int:
    formats = " ".join((m.get("format") or "") for m in rel.get("media", [])).lower()
    for word, rank in FORMAT_RANK:
        if word in formats:
            return rank
    return 5


def square_image(data: bytes, min_side=500, tolerance=0.03) -> Image.Image | None:
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception:  # noqa: BLE001
        return None
    w, h = img.size
    if min(w, h) < min_side or abs(w / h - 1) > tolerance:
        return None
    side = min(w, h)
    left, top = (w - side) // 2, (h - side) // 2
    return img.crop((left, top, left + side, top + side)).convert("RGB")


def jpeg(img: Image.Image, max_side: int) -> bytes:
    if img.size[0] > max_side:
        img = img.resize((max_side, max_side), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=92, optimize=True)
    return buf.getvalue()


def caa_front(release_id: str) -> tuple[bytes, str] | None:
    listing = http(f"https://coverartarchive.org/release/{release_id}")
    for im in (listing or {}).get("images", []):
        if im.get("front") or "Front" in (im.get("types") or []):
            url = (im.get("thumbnails") or {}).get("1200") or im.get("image")
            return http(url, raw=True, headers={"Accept": "*/*"}), url
    return None


def local_front(album_dir: Path) -> list[Path]:
    found = []
    for p in walk(album_dir):
        if p.suffix.lower() in IMAGES and re.search(r"(?i)(^|[\s_\-.])(cover|folder|front)([\s_\-.]|$)", p.stem):
            found.append(p)
    return sorted(found, key=lambda p: (p.parent != album_dir, "front" not in p.stem.lower(), p.name))


def pick_cover(album_dir: Path, release_id: str) -> tuple[Image.Image | None, str, list[str]]:
    notes = []
    rel = mb(f"release/{release_id}", inc="release-groups+media")
    rgid = rel["release-group"]["id"]
    releases, offset = [], 0
    while True:
        page = mb("release", **{"release-group": rgid, "inc": "media", "limit": 100, "offset": offset}) or {}
        releases += page.get("releases", [])
        offset += 100
        if offset >= page.get("release-count", 0) or offset >= 300:
            break
    with_art = [r for r in releases if (r.get("cover-art-archive") or {}).get("front")]
    with_art.sort(key=lambda r: (format_rank(r), r["id"] != release_id, r.get("status") != "Official",
                                 r.get("date") or "9999"))
    local = [(p, square_image(p.read_bytes())) for p in local_front(album_dir)]
    local = [(p, img) for p, img in local if img is not None]
    tried = 0
    for r in with_art:
        rank = format_rank(r)
        if rank > 0 and local:
            p, img = local[0]
            return img, f"your file {p.relative_to(album_dir)} (no square LP cover on MusicBrainz)", notes
        if rank == 9:
            break
        if tried >= 12:
            break
        tried += 1
        got = caa_front(r["id"])
        if not got:
            continue
        img = square_image(got[0])
        line = release_line(r)
        if img is None:
            notes.append(f"skipped {line['formats']} {line['date']} ({r['id']}): not square or too small")
            continue
        return img, f"{line['formats']} {line['date']} {line['country'] or ''} release {r['id']}", notes
    if local:
        p, img = local[0]
        return img, f"your file {p.relative_to(album_dir)}", notes
    return None, "", notes


def cmd_cover(args):
    album_dir = Path(args.dir).resolve()
    audio = [p for p in walk(album_dir) if p.suffix.lower() in AUDIO]
    ids = Counter(read_basic(p)["tags"].get("musicbrainz_albumid") for p in audio)
    release_id = args.release or (ids.most_common(1)[0][0] if ids else None)
    if not release_id:
        die("no MusicBrainz release ID in the tags. Run apply first, or pass --release.")
    img, source, notes = pick_cover(album_dir, release_id)
    for n in notes:
        print(f"  {n}")
    if img is None:
        die("no square front cover found. Every candidate is missing, smaller than 500 px, or not square "
            "(cassette inlays are tall). Put a square cover.jpg in the album folder and run cover again.")
    print(f"cover: {img.size[0]}px square from {source}")
    if args.dry_run:
        return
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    td = tool_dir(album_dir)
    moves, created = [], []
    target = album_dir / "cover.jpg"
    for existing in [album_dir / n for n in os.listdir(album_dir)
                     if re.fullmatch(r"(?i)(cover|folder|front)\.(jpe?g|png|webp|gif|bmp)", n)]:
        dest = album_dir / "Scans" / existing.name
        n = 1
        while dest.exists():
            dest = album_dir / "Scans" / f"{existing.stem} ({n}){existing.suffix}"
            n += 1
        dest.parent.mkdir(exist_ok=True)
        existing.rename(dest)
        moves.append([str(existing), str(dest)])
    target.write_bytes(jpeg(img, int(CONF["COVER_MAX"])))
    created.append(str(target))
    backups = {}
    if CONF["EMBED_COVER"] == "1" and not args.no_embed:
        small = jpeg(img, int(CONF["EMBED_MAX"]))
        size = Image.open(io.BytesIO(small)).size
        for p in audio:
            backups[str(p)] = snapshot(p)
            embed_cover(p, small, size)
        (td / f"tags-{stamp}.pickle").write_bytes(pickle.dumps(backups))
    log = {"kind": "cover", "moves": moves, "created": created,
           "tags": str(td / f"tags-{stamp}.pickle") if backups else None}
    (td / f"undo-{stamp}.json").write_text(json.dumps(log, indent=1, ensure_ascii=False))
    print(f"saved {target.name}" + (f", embedded in {len(backups)} files" if backups else "")
          + f"\nundo log: {td / f'undo-{stamp}.json'}")


def cmd_artist_image(args):
    album_dir = Path(args.dir).resolve()
    key = CONF.get("FANARTTV_API_KEY")
    if not key:
        die(f"no FANARTTV_API_KEY in {CONFIG_FILE}")
    audio = [p for p in walk(album_dir) if p.suffix.lower() in AUDIO]
    if not audio:
        die("no audio files")
    f = open_audio(audio[0])
    fam = family(f)
    ids, name = [], None
    if fam == "vorbis":
        ids, name = list(f.tags.get("MUSICBRAINZ_ALBUMARTISTID", [])), first(f.tags.get("ALBUMARTIST"))
    elif fam == "id3":
        fr = f.tags.getall("TXXX:MusicBrainz Album Artist Id")
        ids = list(fr[0].text) if fr else []
        name = str(f.tags["TPE2"].text[0]) if "TPE2" in f.tags else None
    elif fam == "mp4":
        ids = [bytes(x).decode() for x in f.tags.get("----:com.apple.iTunes:MusicBrainz Album Artist Id", [])]
        name = first(f.tags.get("aART"))
    elif fam == "ape":
        ids = str(f.tags.get("MUSICBRAINZ_ALBUMARTISTID", "")).split("\x00")
        name = str(f.tags.get("Album Artist", "")) or None
    ids = [i for i in ids if i]
    if not ids or ids[0] == VA_ID:
        die("no single album artist ID in the tags (or a Various Artists album). Run apply first.")
    artist_dir = album_dir.parent
    if name and artist_dir.name != safe_name(name):
        print(f"warning: the parent folder '{artist_dir.name}' is not named after '{name}'. "
              f"artist.jpg goes there anyway.")
    dest = artist_dir / "artist.jpg"
    if dest.exists() and not args.force:
        print(f"exists: {dest}")
        return
    data = http(f"https://webservice.fanart.tv/v3/music/{ids[0]}?api_key={key}")
    thumbs = sorted((data or {}).get("artistthumb", []), key=lambda x: -int(x.get("likes", 0)))
    if not thumbs:
        die(f"fanart.tv has no artist image for {name} ({ids[0]})")
    img = Image.open(io.BytesIO(http(thumbs[0]["url"], raw=True, headers={"Accept": "*/*"}))).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=92)
    dest.write_bytes(buf.getvalue())
    print(f"saved {dest} ({img.size[0]}x{img.size[1]})")


# ----------------------------------------------------------------------------- verify

def cmd_verify(args):
    album_dir = Path(args.dir).resolve()
    problems = 0

    def bad(msg):
        nonlocal problems
        problems += 1
        print(f"  {msg}")

    audio = [p for p in walk(album_dir) if p.suffix.lower() in AUDIO]
    print(f"album: {album_dir}")
    if not audio:
        die("no audio files")
    infos = [read_basic(p) for p in audio]
    for i in infos:
        missing = [k for k in ("title", "artist", "album", "albumartist", "tracknumber") if not i["tags"].get(k)]
        if missing:
            bad(f"{Path(i['path']).name}: missing {', '.join(missing)}")
    for k in ("album", "albumartist", "musicbrainz_albumid"):
        values = {i["tags"].get(k) for i in infos}
        if len(values) > 1:
            bad(f"tracks disagree on {k}: {values}")
    keys = Counter((num_pair(i["tags"].get("discnumber"))[0] or 1, num_pair(i["tags"].get("tracknumber"))[0])
                   for i in infos)
    for key, n in keys.items():
        if n > 1:
            bad(f"{n} files share disc {key[0]} track {key[1]}")
    rid = infos[0]["tags"].get("musicbrainz_albumid")
    if rid:
        rel = mb(f"release/{rid}", inc="recordings+media+release-groups+artist-credits")
        if rel:
            tracks = release_tracks(rel)
            have = set(keys)
            gone = [f"{t['disc']}-{t['position']} {t['title']}" for t in tracks if (t["disc"], t["position"]) not in have]
            print(f"  {len(infos)} of {len(tracks)} tracks of {release_line(rel)['formats']} release {rid}")
            if gone:
                bad("missing tracks: " + "; ".join(gone))
            display = credit(rel.get("artist-credit", []))[0]
            year = year_of((rel.get("release-group") or {}).get("first-release-date")) or year_of(rel.get("date"))
            want = safe_name(f"{rel['title']} ({year})" if year else rel["title"])
            if album_dir.name != want:
                bad(f"folder should be named '{want}'")
            if album_dir.parent.name != safe_name(display):
                bad(f"parent folder should be named '{safe_name(display)}'")
    else:
        bad("no MusicBrainz release ID in the tags")
    cover = album_dir / "cover.jpg"
    if not cover.exists():
        bad("no cover.jpg (run: cover)")
    else:
        img = Image.open(cover)
        if abs(img.size[0] / img.size[1] - 1) > 0.03 or min(img.size) < 500:
            bad(f"cover.jpg is {img.size[0]}x{img.size[1]}, not a square of 500 px or more")
    if CONF["EMBED_COVER"] == "1":
        no_pic = [i for i in infos if not i["has_picture"]]
        if no_pic:
            bad(f"{len(no_pic)} files have no embedded cover (run: cover)")
    if CONF.get("FANARTTV_API_KEY") and not (album_dir.parent / "artist.jpg").exists():
        bad("no artist.jpg in the artist folder (run: artist-image)")
    print(f"\n{'OK' if not problems else f'{problems} problems'}")


# ----------------------------------------------------------------------------- cli

def cmd_config(_):
    print(f"config file: {CONFIG_FILE} ({'found' if CONFIG_FILE.exists() else 'not found'})")
    for key in ("ACOUSTID_API_KEY", "FANARTTV_API_KEY"):
        print(f"  {key}: {'set' if CONF.get(key) else 'missing'}")
    for key in ("COVER_MAX", "EMBED_MAX", "EMBED_COVER"):
        print(f"  {key}: {CONF[key]}")
    print(f"  fpcalc: {shutil.which('fpcalc') or 'missing (brew install chromaprint)'}")
    print(f"  mutagen {mutagen.version_string}, python {sys.version.split()[0]}")


def cmd_scan(args):
    print(json.dumps(scan(Path(args.dir).resolve()), indent=1, ensure_ascii=False))


def cmd_mb(args):
    if args.what == "release":
        rel = mb_release(args.id)
        out = {**release_line(rel), "release_group": (rel.get("release-group") or {}).get("id"),
               "original_date": (rel.get("release-group") or {}).get("first-release-date"),
               "tracks": [f"{t['disc']}-{t['number']} {t['title']} ({int(t['length'] // 60)}:{int(t['length'] % 60):02d})"
                          for t in release_tracks(rel)]}
    else:
        q = f'release:"{lucene(args.album)}"' + (f' AND artist:"{lucene(args.artist)}"' if args.artist else "")
        out = [release_line(r) for r in (mb("release", query=q, limit=25) or {}).get("releases", [])]
    print(json.dumps(out, indent=1, ensure_ascii=False))


def cmd_propose(args):
    root = Path(args.dir).resolve()
    dest = Path(args.dest).resolve() if args.dest else root.parent
    plan = propose(root, args.release, dest)
    out = Path(args.out) if args.out else CACHE_DIR / "plans" / f"{safe_name(root.name, 60)}-{dt.datetime.now():%Y%m%d-%H%M%S}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(plan, indent=1, ensure_ascii=False))
    print(f"plan: {out}")
    print(f"{len(plan['files'])} tracks matched, {len(plan['unmatched'])} unmatched files, "
          f"{len(plan['missing_tracks'])} missing tracks, match score {plan['match_score']}, "
          f"{len(plan['warnings'])} warnings.")
    print(f"Review it with: nd.py apply {out}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("config").set_defaults(fn=cmd_config)
    p = sub.add_parser("scan")
    p.add_argument("dir")
    p.set_defaults(fn=cmd_scan)
    p = sub.add_parser("split", help="split a CUE image into one file per track")
    p.add_argument("dir")
    p.add_argument("--execute", action="store_true")
    p.add_argument("--encoding", help="cue sheet text encoding, when the titles come out garbled")
    p.set_defaults(fn=cmd_split)
    p = sub.add_parser("identify")
    p.add_argument("dir")
    p.add_argument("--artist")
    p.add_argument("--album")
    p.add_argument("--fingerprint", action="store_true", help="identify by audio fingerprint (AcoustID)")
    p.add_argument("--limit", type=int, default=8)
    p.set_defaults(fn=cmd_identify)
    p = sub.add_parser("mb")
    msub = p.add_subparsers(dest="what", required=True)
    s = msub.add_parser("release")
    s.add_argument("id")
    s = msub.add_parser("search")
    s.add_argument("artist", nargs="?")
    s.add_argument("album")
    p.set_defaults(fn=cmd_mb)
    p = sub.add_parser("propose")
    p.add_argument("dir")
    p.add_argument("--release", required=True)
    p.add_argument("--dest", help="library root that gets Artist/Album (Year). Default: the parent of DIR")
    p.add_argument("--out")
    p.set_defaults(fn=cmd_propose)
    p = sub.add_parser("apply")
    p.add_argument("plan")
    p.add_argument("--execute", action="store_true")
    p.set_defaults(fn=cmd_apply)
    p = sub.add_parser("cover")
    p.add_argument("dir")
    p.add_argument("--release")
    p.add_argument("--no-embed", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_cover)
    p = sub.add_parser("artist-image")
    p.add_argument("dir")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_artist_image)
    p = sub.add_parser("verify")
    p.add_argument("dir")
    p.set_defaults(fn=cmd_verify)
    p = sub.add_parser("undo")
    p.add_argument("log")
    p.set_defaults(fn=cmd_undo)
    args = ap.parse_args()
    try:
        args.fn(args)
    except RuntimeError as e:
        die(str(e))


if __name__ == "__main__":
    main()
