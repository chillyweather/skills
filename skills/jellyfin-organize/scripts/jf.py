#!/usr/bin/env python3
"""Organize one movie or show folder for Jellyfin.

Standard library only. ffprobe is optional; without it, embedded tracks are not checked.

Commands:
  config                     Show which credentials are set.
  scan DIR                   Inventory the folder and guess what each file is.
  tmdb search|show|movie|find  Look up titles, IDs, and episode lists on TMDB.
  propose DIR --tv|--movie ID  Write a rename plan. Nothing moves.
  apply PLAN [--execute]     Check a plan, then carry it out. Dry run without --execute.
  undo LOG                   Reverse an applied plan.
  verify DIR --tv|--movie ID   Compare the folder against TMDB and report gaps.
  artwork DIR --tv|--movie ID  Download poster, backdrop, logo, and season posters.
  nfo DIR --tv|--movie ID      Write tvshow.nfo, season.nfo, episode .nfo, or movie.nfo.
  subs DIR --tv|--movie ID     Download missing subtitles from OpenSubtitles.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import statistics
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

TOOL_DIR = "_jellyfin-organize"
CONFIG_FILE = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "jellyfin-organize" / "config.env"
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "jellyfin-organize"

VIDEO = {".mkv", ".mp4", ".avi", ".m4v", ".mov", ".wmv", ".ts", ".m2ts", ".webm", ".mpg", ".mpeg", ".flv", ".ogm", ".rmvb"}
SUBS = {".srt", ".ass", ".ssa", ".sub", ".idx", ".vtt", ".sup", ".smi"}
AUDIO = {".mka", ".ac3", ".eac3", ".dts", ".aac", ".flac", ".mp3", ".opus", ".m4a", ".ogg", ".wav"}
IMAGES = {".jpg", ".jpeg", ".png", ".webp", ".tbn"}
OS_JUNK = {".ds_store", "thumbs.db", "desktop.ini"}

LANGS = {
    "en": {"en", "eng", "english", "англ", "английские", "английский"},
    "ru": {"ru", "rus", "russian", "рус", "русские", "русский", "russkie"},
    "ja": {"ja", "jp", "jpn", "jap", "japanese", "яп", "японский"},
    "uk": {"uk", "ukr", "ukrainian", "укр", "украинский"},
    "zh": {"zh", "chi", "zho", "chs", "cht", "chinese"},
    "ko": {"ko", "kor", "korean"},
    "de": {"de", "ger", "deu", "german"},
    "fr": {"fr", "fre", "fra", "french"},
    "es": {"es", "spa", "spanish"},
    "it": {"it", "ita", "italian"},
    "pt": {"pt", "por", "portuguese"},
}
LANG_OF = {alias: code for code, aliases in LANGS.items() for alias in aliases}
# Directory names that describe a container, not a dub or subtitle group.
GENERIC_DIRS = {"subs", "subtitles", "sub", "sound", "sounds", "audio", "dub", "dubs", "voice", "rus sound", "rus subs",
                "eng subs", "субтитры", "озвучка", "звук", "fonts", "attachments"}
FORCED_WORDS = {"signs", "forced", "надписи", "songs"}

EXTRA_WORDS = {
    "clips": {"nc", "ncop", "nced", "creditless", "op", "ed", "opening", "ending"},
    "trailers": {"pv", "cm", "trailer", "teaser", "preview", "promo", "spot"},
    "extras": {"menu", "menus", "bonus", "extra", "extras", "making", "interview"},
}
SPECIAL_WORDS = {"ova", "oad", "ona", "sp", "special", "specials", "recap"}
EXTRA_DIRS = {"behind the scenes", "deleted scenes", "interviews", "scenes", "samples", "shorts", "featurettes",
              "clips", "other", "extras", "trailers", "theme-music", "backdrops"}
ART_STEMS = {"poster", "folder", "cover", "backdrop", "fanart", "logo", "clearlogo", "banner", "thumb", "landscape",
             "tvshow", "season", "movie", "show", "default", "background", "art"}


# ----------------------------------------------------------------------------- config and http

def load_config() -> dict:
    conf = {}
    if CONFIG_FILE.exists():
        for line in CONFIG_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                conf[key.strip()] = value.strip().strip("'\"")
    for key in list(conf) + ["TMDB_API_KEY", "TMDB_READ_TOKEN", "TMDB_LANGUAGE", "OPENSUBTITLES_API_KEY",
                             "OPENSUBTITLES_USERNAME", "OPENSUBTITLES_PASSWORD", "OPENSUBTITLES_USER_AGENT", "SUB_LANGS"]:
        if os.environ.get(key):
            conf[key] = os.environ[key]
    conf.setdefault("TMDB_LANGUAGE", "en-US")
    conf.setdefault("SUB_LANGS", "en,ru")
    conf.setdefault("OPENSUBTITLES_USER_AGENT", "jellyfin-organize v1.0")
    return conf


CONF = load_config()


def die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def http(url: str, *, method="GET", headers=None, body=None, raw=False, retries=3):
    data = json.dumps(body).encode() if body is not None else None
    hdrs = {"Accept": "application/json", **(headers or {})}
    if data is not None:
        hdrs["Content-Type"] = "application/json"
    for attempt in range(retries):
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                payload = resp.read()
                return payload if raw else json.loads(payload or b"null")
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries - 1:
                time.sleep(float(e.headers.get("Retry-After") or 2))
                continue
            detail = e.read().decode(errors="replace")[:300]
            raise RuntimeError(f"HTTP {e.code} for {url.split('?')[0]}: {detail}") from None
        except urllib.error.URLError as e:
            if attempt < retries - 1:
                time.sleep(2)
                continue
            raise RuntimeError(f"network error for {url.split('?')[0]}: {e.reason}") from None


# ----------------------------------------------------------------------------- TMDB

TMDB = "https://api.themoviedb.org/3"
IMG = "https://image.tmdb.org/t/p/original"


def tmdb(path: str, **params):
    headers = {}
    if CONF.get("TMDB_READ_TOKEN"):
        headers["Authorization"] = f"Bearer {CONF['TMDB_READ_TOKEN']}"
    elif CONF.get("TMDB_API_KEY"):
        params["api_key"] = CONF["TMDB_API_KEY"]
    else:
        die(f"no TMDB credentials. Put TMDB_API_KEY=... in {CONFIG_FILE}")
    params.setdefault("language", CONF["TMDB_LANGUAGE"])
    params = {k: v for k, v in params.items() if v is not None}
    return http(f"{TMDB}{path}?{urllib.parse.urlencode(params)}", headers=headers)


def year_of(date: str | None) -> int | None:
    return int(date[:4]) if date and len(date) >= 4 and date[:4].isdigit() else None


def tmdb_show(show_id: int) -> dict:
    """Series details with every season's episode list."""
    d = tmdb(f"/tv/{show_id}", append_to_response="external_ids")
    numbers = [s["season_number"] for s in d.get("seasons", [])]
    seasons = {}
    for i in range(0, len(numbers), 19):
        chunk = numbers[i:i + 19]
        extra = tmdb(f"/tv/{show_id}", append_to_response=",".join(f"season/{n}" for n in chunk))
        for n in chunk:
            seasons[n] = extra.get(f"season/{n}") or {}
    d["seasons_full"] = []
    for s in d.get("seasons", []):
        full = seasons.get(s["season_number"], {})
        d["seasons_full"].append({
            "season_number": s["season_number"],
            "name": s.get("name"),
            "air_date": s.get("air_date"),
            "poster_path": s.get("poster_path"),
            "overview": full.get("overview") or s.get("overview"),
            "episodes": [{
                "episode_number": e["episode_number"],
                "name": e.get("name"),
                "air_date": e.get("air_date"),
                "overview": e.get("overview"),
                "runtime": e.get("runtime"),
                "still_path": e.get("still_path"),
                "id": e.get("id"),
            } for e in full.get("episodes", [])],
        })
    return d


def tmdb_movie(movie_id: int) -> dict:
    return tmdb(f"/movie/{movie_id}", append_to_response="external_ids")


def show_summary(d: dict) -> dict:
    return {
        "id": d["id"],
        "name": d.get("name"),
        "original_name": d.get("original_name"),
        "year": year_of(d.get("first_air_date")),
        "status": d.get("status"),
        "external_ids": {k: v for k, v in (d.get("external_ids") or {}).items() if k in ("imdb_id", "tvdb_id") and v},
        "canonical_folder": canonical_name(d.get("name"), year_of(d.get("first_air_date")), d["id"]),
        "seasons": [{
            "season": s["season_number"],
            "name": s["name"],
            "episodes": len(s["episodes"]),
            "aired": sum(1 for e in s["episodes"] if aired(e.get("air_date"))),
            "titles": {e["episode_number"]: e["name"] for e in s["episodes"]},
        } for s in d["seasons_full"]],
    }


def aired(date: str | None) -> bool:
    return bool(date) and date <= dt.date.today().isoformat()


# ----------------------------------------------------------------------------- names

def sanitize(name: str) -> str:
    name = re.sub(r":\s", " - ", name)
    name = re.sub(r'[<>:"/\\|?*]', "-", name)
    name = re.sub(r"\s+", " ", name).strip().rstrip(".")
    return name


def canonical_name(title: str, year: int | None, tmdb_id: int) -> str:
    return sanitize(f"{title} ({year}) [tmdbid-{tmdb_id}]" if year else f"{title} [tmdbid-{tmdb_id}]")


def episode_code(season: int, ep: int, ep_end: int | None = None, width: int = 2) -> str:
    code = f"S{season:02d}E{ep:0{width}d}"
    return code + (f"-E{ep_end:0{width}d}" if ep_end else "")


def tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^\w]+", text.lower()) if t]


def word_tokens(text: str) -> set[str]:
    """Tokens with trailing digits dropped, so NCOP1 and OVA2 match their keywords."""
    return {re.sub(r"\d+$", "", t) or t for t in tokens(text)}


def debracket(stem: str) -> str:
    return re.sub(r"[\[\(\{【][^\]\)\}】]*[\]\)\}】]", " ", stem)


def guess_season_from_text(text: str) -> int | None:
    for pat in (r"\b(\d{1,2})(?:st|nd|rd|th)[ ._-]+season\b", r"\bseason[ ._]*(\d{1,2})\b", r"\bсезон[ ._]*(\d{1,2})\b",
                r"(?:^|[ ._\-\[])s(\d{1,2})(?=$|[ ._\-\]])"):
        m = re.search(pat, text, re.I)
        if m:
            return int(m.group(1))
    return None


def guess(rel: Path) -> dict:
    """Guess what a file is from its name and the folders above it."""
    stem = rel.stem
    g: dict = {"kind": "episode", "confidence": "low"}
    dir_tokens = set(t for part in rel.parts[:-1] for t in word_tokens(part))
    stem_tokens = word_tokens(debracket(stem))

    m = re.search(r"\bS(\d{1,2})[ ._-]?E(\d{1,4})(?:[-~]?E?(\d{1,4}))?", stem, re.I)
    if m:
        g.update(season=int(m.group(1)), episode=int(m.group(2)), confidence="high")
        if m.group(3) and int(m.group(3)) > int(m.group(2)):
            g["episode_end"] = int(m.group(3))
    if "episode" not in g:
        m = re.search(r"\b(\d{1,2})x(\d{2,3})\b", stem)
        if m:
            g.update(season=int(m.group(1)), episode=int(m.group(2)), confidence="high")
    if "episode" not in g:
        clean = debracket(stem)
        for pat, conf in ((r"\s[-–]\s(\d{1,4})(?:v\d)?(?=\s|$)", "high"),
                          (r"\bE[Pp]?\.?\s?(\d{1,4})(?:v\d)?\b", "high"),
                          (r"第\s*(\d{1,4})\s*[話话集]", "high"),
                          (r"(?i)(?:серия|эпизод)[ ._]*(\d{1,4})", "high"),
                          (r"(?i)\b(?:ova|oad|ona|sp|special)[ ._-]*(\d{1,3})\b", "medium")):
            m = re.search(pat, clean)
            if m:
                g.update(absolute=int(m.group(1)), confidence=conf)
                break
    if "episode" not in g and "absolute" not in g:
        m = re.search(r"\[(\d{1,4})(?:v\d)?\]", stem)
        if m:
            g.update(absolute=int(m.group(1)), confidence="medium")
    if "episode" not in g and "absolute" not in g:
        clean = re.sub(r"(?i)\b[hx]\.?26[45]\b|\b\d\.\d\b|\b\d{3,4}p\b|\b\d+bit\b", " ", debracket(stem))
        nums = [t for t in re.split(r"[ ._\-]+", clean) if re.fullmatch(r"\d{1,4}(v\d)?", t)]
        nums = [n for n in nums if not re.fullmatch(r"(19|20)\d\d", n)]
        if nums:
            g.update(absolute=int(re.sub(r"v\d$", "", nums[-1])), confidence="low")

    if "season" not in g:
        season = guess_season_from_text(debracket(stem)) or next(
            (s for s in (guess_season_from_text(p) for p in reversed(rel.parts[:-1])) if s is not None), None)
        if season is not None:
            g["season"] = season
            if "absolute" in g:
                g["episode"] = g.pop("absolute")
    if "specials" in dir_tokens or stem_tokens & SPECIAL_WORDS:
        g["kind"] = "special"
    for extra_type, words in EXTRA_WORDS.items():
        if stem_tokens & words or (dir_tokens & words and not stem_tokens & SPECIAL_WORDS):
            g["kind"], g["extra_type"] = "extra", extra_type
            break
    if "sample" in stem_tokens or "sample" in dir_tokens:
        g["kind"] = "sample"
    return g


def lang_guess(rel: Path, video_stem: str | None = None, video_dirs: set[str] = frozenset()) -> dict:
    """Language, forced flag, and group label for a subtitle or audio file.

    The label comes only from folders below the ones that hold videos, such as RUS Sound/[AniLibria].
    """
    name_part = rel.stem[len(video_stem):] if video_stem and rel.stem.startswith(video_stem) else rel.stem
    name_tokens = tokens(name_part)
    parents = rel.parts[:-1]
    dir_parts = [parents[i] for i in range(len(parents)) if str(Path(*parents[:i + 1])) not in video_dirs]
    lang = next((LANG_OF[t] for t in reversed(name_tokens) if t in LANG_OF), None)
    if lang is None:
        for part in reversed(dir_parts):
            lang = next((LANG_OF[t] for t in tokens(part) if t in LANG_OF), None)
            if lang:
                break
    all_tokens = set(name_tokens) | set(t for p in dir_parts for t in tokens(p))
    label = None
    for part in reversed(dir_parts):
        low = part.lower().strip()
        part_tokens = tokens(part)
        if low in GENERIC_DIRS or not part_tokens or all(t in LANG_OF or t in GENERIC_DIRS or t in FORCED_WORDS
                                                         or t in ("sound", "subs", "sub", "audio") for t in part_tokens):
            continue
        if guess_season_from_text(part) is not None:
            continue
        label = re.sub(r"[\[\]\(\)\{\}.]", " ", part)
        label = re.sub(r"\s+", " ", label).strip()
        break
    return {"lang": lang, "forced": bool(all_tokens & FORCED_WORDS), "label": label or None}


# ----------------------------------------------------------------------------- scan

def probe(path: Path) -> dict | None:
    if not shutil.which("ffprobe"):
        return None
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "stream=index,codec_type,codec_name:stream_tags=language,title:stream_disposition=forced:format=duration",
             "-of", "json", str(path)], capture_output=True, text=True, timeout=120)
        data = json.loads(out.stdout or "{}")
    except (subprocess.TimeoutExpired, json.JSONDecodeError):
        return None
    info = {"duration": round(float(data.get("format", {}).get("duration") or 0)), "audio": [], "subs": []}
    for s in data.get("streams", []):
        tags = s.get("tags", {})
        lang = tags.get("language", "und").lower()
        entry = {"lang": LANG_OF.get(lang, lang), "title": tags.get("title")}
        if entry["lang"] in ("und", None) and entry["title"]:
            entry["lang"] = next((LANG_OF[t] for t in tokens(entry["title"]) if t in LANG_OF), "und")
        if s.get("codec_type") == "audio":
            info["audio"].append(entry)
        elif s.get("codec_type") == "subtitle":
            entry["forced"] = bool(s.get("disposition", {}).get("forced"))
            info["subs"].append(entry)
    return info


def walk(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d != TOOL_DIR)
        for f in sorted(filenames):
            if f.startswith(".") or f.lower() in OS_JUNK:
                continue
            p = Path(dirpath) / f
            if not p.is_symlink():
                yield p


def kind_of(p: Path) -> str:
    ext = p.suffix.lower()
    if ext in VIDEO:
        return "video"
    if ext in SUBS:
        return "subtitle"
    if ext in AUDIO:
        return "audio"
    if ext in IMAGES:
        return "image"
    if ext == ".nfo":
        return "nfo"
    return "other"


def scan(root: Path, do_probe=True) -> dict:
    files = {"video": [], "subtitle": [], "audio": [], "image": [], "nfo": [], "other": []}
    for p in walk(root):
        rel = p.relative_to(root)
        entry = {"path": str(rel), "size": p.stat().st_size}
        k = kind_of(p)
        if k == "video":
            entry["guess"] = guess(rel)
            if do_probe:
                entry["probe"] = probe(p)
        elif k in ("subtitle", "audio"):
            entry["guess"] = guess(rel)
            entry["lang"] = lang_guess(rel)
        files[k].append(entry)
    return {"root": str(root), "counts": {k: len(v) for k, v in files.items()}, **files}


# ----------------------------------------------------------------------------- propose

def ep_key(g: dict):
    if "episode" in g:
        return (g.get("season"), g["episode"])
    if "absolute" in g:
        return (None, g["absolute"])
    return None


def absolute_map(show: dict) -> dict:
    """Absolute episode number -> (season, episode) over the regular seasons."""
    mapping, n = {}, 0
    for s in sorted(show["seasons_full"], key=lambda s: s["season_number"]):
        if s["season_number"] == 0:
            continue
        for e in s["episodes"]:
            n += 1
            mapping[n] = (s["season_number"], e["episode_number"])
    return mapping


def propose(root: Path, tv: int | None, movie: int | None, season_override: int | None, offset: int,
            do_probe: bool) -> dict:
    inv = scan(root, do_probe)
    moves, warnings, unmatched = [], [], []
    claimed = set()

    def move(src: str, dst: str, why: str):
        claimed.add(src)
        moves.append({"from": src, "to": dst, "why": why})

    videos = inv["video"]
    durations = [v["probe"]["duration"] for v in videos if v.get("probe") and v["probe"]["duration"]]
    median = statistics.median(durations) if durations else 0

    if tv:
        show = tmdb_show(tv)
        title = sanitize(show["name"])
        new_root = canonical_name(show["name"], year_of(show.get("first_air_date")), tv)
        seasons = {s["season_number"]: s for s in show["seasons_full"]}
        counts = {n: len(s["episodes"]) for n, s in seasons.items()}
        regular = [n for n in seasons if n > 0]
        absmap = absolute_map(show)
        width = lambda s: 3 if counts.get(s, 0) >= 100 else 2
        video_targets = {}

        for v in videos:
            g = v["guess"]
            src = v["path"]
            ext = Path(src).suffix.lower()
            if v.get("probe") and median and v["probe"]["duration"] > 2.5 * median:
                warnings.append(f"{src}: runs {v['probe']['duration'] // 60} min against a median of "
                                f"{int(median) // 60} min. It may be a movie that belongs in a Movies library.")
            if g["kind"] == "sample":
                move(src, f"{TOOL_DIR}/leftovers/{src}", "sample, Jellyfin ignores it")
                continue
            if g["kind"] == "extra":
                move(src, f"{g['extra_type']}/{Path(src).name}", f"extra ({g['extra_type']})")
                continue
            if g["kind"] == "special":
                n = g.get("episode") or g.get("absolute")
                specials = {e["episode_number"]: e for e in seasons.get(0, {}).get("episodes", [])}
                if n and n in specials:
                    dst = f"Season 00/{title} {episode_code(0, n)}{ext}"
                    move(src, dst, f"special, check it is TMDB S00E{n:02d} '{specials[n]['name']}'")
                    video_targets[src] = dst
                    warnings.append(f"{src}: matched to special S00E{n:02d} '{specials[n]['name']}' by number only. "
                                    f"Check it.")
                else:
                    unmatched.append(src)
                    warnings.append(f"{src}: special with no matching TMDB S00 entry. Give it a descriptive name "
                                    f"in Season 00, or map it by hand.")
                continue
            season, ep = g.get("season"), g.get("episode")
            if season_override is not None and season is None:
                season = season_override
            if ep is None and g.get("absolute") is not None:
                ep = g["absolute"]
                if season is None and len(regular) == 1:
                    season = regular[0]
            if ep is None:
                unmatched.append(src)
                warnings.append(f"{src}: no episode number found.")
                continue
            ep -= offset
            if season is None or (season in counts and ep > counts[season] and ep in absmap
                                  and absmap[ep][0] == season):
                if ep not in absmap:
                    unmatched.append(src)
                    warnings.append(f"{src}: absolute episode {ep} is beyond the {len(absmap)} episodes on TMDB.")
                    continue
                season, ep = absmap[ep]
            if season not in counts:
                warnings.append(f"{src}: season {season} does not exist on TMDB.")
            elif ep > counts[season]:
                warnings.append(f"{src}: S{season:02d}E{ep:02d} is beyond the {counts[season]} episodes TMDB lists "
                                f"for season {season}.")
            if g["confidence"] == "low":
                warnings.append(f"{src}: episode number guessed with low confidence. Check it.")
            end = g.get("episode_end")
            end = end - offset if end else None
            dst = f"Season {season:02d}/{title} {episode_code(season, ep, end, width(season))}{ext}"
            move(src, dst, f"episode {episode_code(season, ep, end)}")
            video_targets[src] = dst
    else:
        m = tmdb_movie(movie)
        new_root = canonical_name(m["title"], year_of(m.get("release_date")), movie)
        video_targets = {}
        mains = []
        for v in videos:
            g, src = v["guess"], v["path"]
            if g["kind"] == "sample":
                move(src, f"{TOOL_DIR}/leftovers/{src}", "sample, Jellyfin ignores it")
            elif g["kind"] == "extra":
                move(src, f"{g['extra_type']}/{Path(src).name}", f"extra ({g['extra_type']})")
            else:
                mains.append(v)
        parts = [v for v in mains if re.search(r"(?:cd|dvd|part|pt|disc|disk)[ ._-]?\d", Path(v["path"]).stem, re.I)]
        if len(parts) >= 2 and len(parts) == len(mains):
            for v in parts:
                n = re.search(r"(?:cd|dvd|part|pt|disc|disk)[ ._-]?(\d)", Path(v["path"]).stem, re.I).group(1)
                dst = f"{new_root}-part{n}{Path(v['path']).suffix.lower()}"
                move(v["path"], dst, f"movie part {n}")
                video_targets[v["path"]] = dst
        elif mains:
            mains.sort(key=lambda v: -v["size"])
            for i, v in enumerate(mains):
                ext = Path(v["path"]).suffix.lower()
                if i == 0 and len(mains) == 1:
                    dst = f"{new_root}{ext}"
                else:
                    res = re.search(r"(2160|1080|720|480)p", v["path"], re.I)
                    label = f"{res.group(1)}p" if res else f"Version {i + 1}"
                    dst = f"{new_root} - {label}{ext}"
                    warnings.append(f"{v['path']}: several main videos, treated as versions. Check the labels.")
                move(v["path"], dst, "movie")
                video_targets[v["path"]] = dst
        else:
            warnings.append("No main movie video found.")

    # Sidecars: first by shared file stem, then by episode number.
    by_dir_stem = {}
    by_key = {}
    for v in videos:
        if v["path"] not in video_targets:
            continue
        p = Path(v["path"])
        by_dir_stem.setdefault(str(p.parent), []).append((p.stem, v["path"]))
        k = ep_key(v["guess"])
        if k and v["guess"]["kind"] == "episode":
            by_key.setdefault(k[1], []).append((k[0], v["path"]))
    single_video = next(iter(video_targets)) if movie and len(video_targets) == 1 else None
    video_dirs = set()
    for v in videos:
        parents = Path(v["path"]).parts[:-1]
        video_dirs |= {str(Path(*parents[:i + 1])) for i in range(len(parents))}

    def is_xml(rel: str) -> bool:
        with open(root / rel, "rb") as fh:
            return fh.read(200).lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"<")

    def is_library_art(p: Path) -> bool:
        in_place = len(p.parts) == 1 or (len(p.parts) == 2 and re.fullmatch(r"Season \d{2,}", p.parts[0]))
        return bool(in_place) and (p.stem.lower() in ART_STEMS or
                                   re.fullmatch(r"season(\d+|-specials)-\w+", p.stem, re.I) is not None)

    for kind in ("subtitle", "audio", "image", "nfo"):
        for f in inv[kind]:
            src = f["path"]
            p = Path(src)
            match = None
            for stem, vpath in sorted(by_dir_stem.get(str(p.parent), []), key=lambda x: -len(x[0])):
                if p.stem == stem or (p.stem.startswith(stem) and p.stem[len(stem)] in ".-_ ["):
                    match = (vpath, p.name[len(stem):])
                    break
            if match and not (kind == "nfo" and not is_xml(src)):
                vpath, suffix = match
                new_stem = Path(video_targets[vpath]).with_suffix("")
                move(src, f"{new_stem}{suffix}", f"sidecar of {vpath}")
                if kind in ("subtitle", "audio") and not lang_guess(Path(Path(suffix).stem))["lang"]:
                    warnings.append(f"{src}: no language in the name. Jellyfin will list it as unknown. "
                                    f"Add .en, .ru, or similar before the extension if you know it.")
                continue
            if kind in ("image", "nfo") and is_library_art(p) and (kind == "image" or is_xml(src)):
                continue
            if kind in ("subtitle", "audio"):
                g = f["guess"]
                k = ep_key(g)
                vpath = single_video
                if k and not vpath:
                    cands = by_key.get(k[1], [])
                    if k[0] is not None:
                        cands = [c for c in cands if c[0] in (k[0], None)] or cands
                    if len(cands) == 1:
                        vpath = cands[0][1]
                if vpath:
                    info = lang_guess(p, video_dirs=video_dirs)
                    bits = [x for x in (info["label"], info["lang"] or "und") if x]
                    if info["forced"]:
                        bits.append("forced")
                    new_stem = Path(video_targets[vpath]).with_suffix("")
                    move(src, f"{new_stem}.{'.'.join(bits)}{p.suffix.lower()}", f"{kind} for {vpath}")
                    if not info["lang"]:
                        warnings.append(f"{src}: language unknown, named 'und'. Rename it if you know the language.")
                    continue
            move(src, f"{TOOL_DIR}/leftovers/{src}", f"unpaired {kind}")
    for f in inv["other"]:
        move(f["path"], f"{TOOL_DIR}/leftovers/{f['path']}", "not media")
    fonts = [f for f in inv["other"] if Path(f["path"]).suffix.lower() in (".ttf", ".otf", ".ttc")]
    if fonts:
        warnings.append(f"{len(fonts)} font files moved to leftovers. Jellyfin ignores loose fonts. Styled .ass "
                        f"subtitles may need these fonts in the server's fallback font folder.")

    moves = [m for m in moves if m["from"] != m["to"]]
    return {
        "root": str(root),
        "new_root_name": new_root,
        "type": "tv" if tv else "movie",
        "tmdb_id": tv or movie,
        "moves": moves,
        "unmatched": unmatched,
        "warnings": warnings,
    }


# ----------------------------------------------------------------------------- apply and undo

def safe_rel(root: Path, rel: str) -> Path:
    p = Path(rel)
    if p.is_absolute() or ".." in p.parts or not rel.strip():
        raise ValueError(f"unsafe path: {rel!r}")
    full = (root / p)
    if not str(full.resolve()).startswith(str(root.resolve()) + os.sep):
        raise ValueError(f"path leaves the folder: {rel!r}")
    return full


def check_plan(plan: dict) -> tuple[Path, list[str]]:
    root = Path(plan["root"])
    errors = []
    if not root.is_dir():
        return root, [f"folder not found: {root}"]
    sources = {m["from"].lower() for m in plan["moves"]}
    seen = {}
    for m in plan["moves"]:
        try:
            src, dst = safe_rel(root, m["from"]), safe_rel(root, m["to"])
        except ValueError as e:
            errors.append(str(e))
            continue
        if not src.exists():
            errors.append(f"missing source: {m['from']}")
        elif src.is_symlink():
            errors.append(f"source is a symlink: {m['from']}")
        key = m["to"].lower()
        if key in seen:
            errors.append(f"two files go to {m['to']}: {seen[key]} and {m['from']}")
        seen[key] = m["from"]
        if dst.exists() and key not in sources and key != m["from"].lower():
            errors.append(f"target exists: {m['to']}")
    new_name = plan.get("new_root_name")
    if new_name and new_name != root.name:
        if "/" in new_name or new_name in (".", ".."):
            errors.append(f"bad folder name: {new_name}")
        elif (root.parent / new_name).exists() and (root.parent / new_name).resolve() != root.resolve():
            errors.append(f"folder already exists: {root.parent / new_name}")
    return root, errors


def prune_empty_dirs(root: Path):
    for dirpath, dirnames, filenames in os.walk(root, topdown=False):
        d = Path(dirpath)
        if d == root or TOOL_DIR in d.relative_to(root).parts:
            continue
        rest = [f for f in os.listdir(d) if f.lower() not in OS_JUNK]
        if not rest:
            for f in os.listdir(d):
                (d / f).unlink()
            d.rmdir()


def run_moves(root: Path, pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Move through a staging folder so swaps and case-only renames are safe. Returns the pairs done."""
    stage = root / TOOL_DIR / f".staging-{os.getpid()}"
    stage.mkdir(parents=True, exist_ok=True)
    staged, done = [], []
    try:
        for i, (src, dst) in enumerate(pairs):
            (root / src).rename(stage / str(i))
            staged.append((i, src, dst))
        for i, src, dst in staged:
            target = root / dst
            target.parent.mkdir(parents=True, exist_ok=True)
            (stage / str(i)).rename(target)
            done.append((i, src, dst))
    except OSError as e:
        # Put every file back where it was, then stop.
        for i, src, dst in reversed(done):
            (root / dst).rename(root / src)
        for i, src, dst in staged:
            if (stage / str(i)).exists():
                (root / src).parent.mkdir(parents=True, exist_ok=True)
                (stage / str(i)).rename(root / src)
        stage.rmdir()
        raise RuntimeError(f"move failed, all files restored: {e}") from None
    stage.rmdir()
    return [(src, dst) for _, src, dst in done]


def cmd_apply(args):
    plan = json.loads(Path(args.plan).read_text())
    root, errors = check_plan(plan)
    for m in plan["moves"]:
        print(f"  {m['from']}\n    -> {m['to']}   ({m.get('why', '')})")
    new_name = plan.get("new_root_name")
    if new_name and new_name != root.name:
        print(f"\nfolder: {root.name}\n    -> {new_name}")
    print(f"\n{len(plan['moves'])} moves.")
    for w in plan.get("warnings", []):
        print(f"warning: {w}")
    for u in plan.get("unmatched", []):
        print(f"unmatched, left in place: {u}")
    if errors:
        for e in errors:
            print(f"ERROR: {e}")
        die("plan has errors, nothing moved")
    if not args.execute:
        print("\nDry run. Nothing moved. Add --execute to apply.")
        return
    tool = root / TOOL_DIR
    tool.mkdir(exist_ok=True)
    (tool / ".ignore").touch()
    done = run_moves(root, [(m["from"], m["to"]) for m in plan["moves"]])
    prune_empty_dirs(root)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    log = {"root_before": str(root), "root_after": str(root), "moves": [{"from": s, "to": d} for s, d in done]}
    final = root
    if new_name and new_name != root.name:
        final = root.parent / new_name
        root.rename(final)
        log["root_after"] = str(final)
    log_path = final / TOOL_DIR / f"undo-{stamp}.json"
    log_path.write_text(json.dumps(log, indent=2, ensure_ascii=False))
    print(f"\nDone. {len(done)} files moved.\nfolder: {final}\nundo log: {log_path}")


def cmd_undo(args):
    log_path = Path(args.log)
    log = json.loads(log_path.read_text())
    before, after = Path(log["root_before"]), Path(log["root_after"])
    root = after if after.exists() else before
    pairs = []
    for m in reversed(log["moves"]):
        if (root / m["to"]).exists() and not (root / m["from"]).exists():
            pairs.append((m["to"], m["from"]))
        else:
            print(f"skip, already moved or changed: {m['to']}")
    run_moves(root, pairs)
    prune_empty_dirs(root)
    if log_path.exists():
        log_path.rename(log_path.with_suffix(".undone.json"))
    if root != before and not before.exists():
        root.rename(before)
        root = before
    print(f"Undone. {len(pairs)} files moved back.\nfolder: {root}")


# ----------------------------------------------------------------------------- verify

EP_RE = re.compile(r"\bS(\d{2,})E(\d{2,})(?:-E(\d{2,}))?", re.I)


def episode_files(root: Path) -> list[tuple[Path, int, int, int | None]]:
    out = []
    for p in walk(root):
        if kind_of(p) != "video":
            continue
        rel = p.relative_to(root)
        m = EP_RE.search(p.stem)
        if m and len(rel.parts) == 2 and re.fullmatch(r"Season \d+", rel.parts[0]):
            out.append((p, int(m.group(1)), int(m.group(2)), int(m.group(3)) if m.group(3) else None))
    return out


def sub_langs(video: Path, do_probe: bool) -> set[str]:
    langs = set()
    for f in video.parent.iterdir():
        if f.suffix.lower() in SUBS and f.stem.startswith(video.stem + "."):
            info = lang_guess(Path(f.name), video.stem)
            if info["lang"] and not info["forced"]:
                langs.add(info["lang"])
    if do_probe:
        info = probe(video)
        if info:
            langs |= {s["lang"] for s in info["subs"] if not s.get("forced") and s["lang"] != "und"}
    return langs


def cmd_verify(args):
    root = Path(args.dir).resolve()
    want = [x.strip() for x in CONF["SUB_LANGS"].split(",") if x.strip()]
    problems = 0
    print(f"folder: {root.name}")
    if args.tv:
        show = tmdb_show(args.tv)
        expected = canonical_name(show["name"], year_of(show.get("first_air_date")), args.tv)
        if root.name != expected:
            print(f"  folder name should be: {expected}")
            problems += 1
        files = episode_files(root)
        have = {}
        for p, s, e, end in files:
            for n in range(e, (end or e) + 1):
                have.setdefault((s, n), []).append(p)
        for s in show["seasons_full"]:
            n = s["season_number"]
            eps = s["episodes"]
            aired_eps = [e["episode_number"] for e in eps if aired(e.get("air_date"))]
            present = sorted(e for (sn, e) in have if sn == n)
            if not present and n == 0:
                continue
            missing = [e for e in aired_eps if (n, e) not in have]
            print(f"  Season {n:02d}: {len(present)} of {len(aired_eps)} aired episodes"
                  + (f" ({len(eps) - len(aired_eps)} not aired yet)" if len(eps) > len(aired_eps) else ""))
            if missing and n != 0:
                print(f"    missing: {', '.join(f'E{e:02d}' for e in missing)}")
                problems += 1
        known = {(s["season_number"], e["episode_number"]) for s in show["seasons_full"] for e in s["episodes"]}
        for key, paths in sorted(have.items()):
            if key not in known:
                print(f"  not on TMDB: S{key[0]:02d}E{key[1]:02d} {paths[0].relative_to(root)}")
                problems += 1
            if len(paths) > 1:
                print(f"  duplicate S{key[0]:02d}E{key[1]:02d}: {', '.join(str(p.relative_to(root)) for p in paths)}")
                problems += 1
        listed = {p for p, *_ in files}
        for p in walk(root):
            rel = p.relative_to(root)
            if kind_of(p) == "video" and p not in listed and rel.parts[0].lower() not in EXTRA_DIRS \
                    and not (len(rel.parts) >= 2 and rel.parts[-2].lower() in EXTRA_DIRS):
                print(f"  video Jellyfin may not place: {rel}")
                problems += 1
        videos = [p for p, *_ in files]
        for p in walk(root):
            if kind_of(p) == "video" and not EP_RE.search(p.stem) and p.parent.name.startswith("Season"):
                print(f"  no SxxEyy in name: {p.relative_to(root)}")
                problems += 1
        art = ["poster", "backdrop", "logo"]
        nfo_needed = ["tvshow.nfo"]
    else:
        m = tmdb_movie(args.movie)
        expected = canonical_name(m["title"], year_of(m.get("release_date")), args.movie)
        if root.name != expected:
            print(f"  folder name should be: {expected}")
            problems += 1
        videos = [p for p in root.iterdir() if kind_of(p) == "video" and not p.name.startswith(".")]
        if not videos:
            print("  no movie video in the folder root")
            problems += 1
        for p in videos:
            if not p.stem.startswith(root.name):
                print(f"  video name must start with the folder name: {p.name}")
                problems += 1
        art = ["poster", "backdrop", "logo"]
        nfo_needed = ["movie.nfo"]

    for a in art:
        if not any((root / f"{a}{ext}").exists() for ext in (".jpg", ".png", ".webp")):
            print(f"  no {a} image (run: artwork)")
            problems += 1
    for n in nfo_needed:
        if not (root / n).exists():
            print(f"  no {n} (run: nfo)")
            problems += 1
    no_nfo = [v for v in videos if args.tv and not v.with_suffix(".nfo").exists()]
    if no_nfo:
        print(f"  {len(no_nfo)} episodes have no .nfo (run: nfo)")
        problems += 1
    gaps = {lang: [] for lang in want}
    for v in videos:
        langs = sub_langs(v, not args.no_probe)
        for lang in want:
            if lang not in langs:
                gaps[lang].append(v)
    for lang, vs in gaps.items():
        if vs:
            print(f"  {len(vs)} of {len(videos)} videos have no '{lang}' subtitles (run: subs)")
            problems += 1
    print(f"\n{'OK' if not problems else f'{problems} problems'}")


# ----------------------------------------------------------------------------- artwork

def download(url: str, dest: Path, force: bool) -> bool:
    if dest.exists() and not force:
        return False
    data = http(url, raw=True, headers={"Accept": "*/*"})
    dest.write_bytes(data)
    return True


def pick_logo(images: dict) -> str | None:
    logos = [l for l in images.get("logos", []) if l["file_path"].endswith(".png")]
    logos.sort(key=lambda l: (l.get("iso_639_1") == "en", l.get("vote_average", 0), l.get("vote_count", 0)),
               reverse=True)
    return logos[0]["file_path"] if logos else None


def cmd_artwork(args):
    root = Path(args.dir).resolve()
    got = []
    if args.tv:
        show = tmdb_show(args.tv)
        images = tmdb(f"/tv/{args.tv}/images", include_image_language="en,null", language=None)
        targets = [("poster.jpg", show.get("poster_path")), ("backdrop.jpg", show.get("backdrop_path")),
                   ("logo.png", pick_logo(images))]
        for s in show["seasons_full"]:
            folder = root / f"Season {s['season_number']:02d}"
            if folder.is_dir() and s.get("poster_path"):
                targets.append((f"{folder.name}/poster.jpg", s["poster_path"]))
        if args.episode_thumbs:
            for p, sn, e, _ in episode_files(root):
                ep = next((x for s in show["seasons_full"] if s["season_number"] == sn
                           for x in s["episodes"] if x["episode_number"] == e), None)
                if ep and ep.get("still_path"):
                    targets.append((str((p.parent / f"{p.stem}-thumb.jpg").relative_to(root)), ep["still_path"]))
    else:
        m = tmdb_movie(args.movie)
        images = tmdb(f"/movie/{args.movie}/images", include_image_language="en,null", language=None)
        targets = [("poster.jpg", m.get("poster_path")), ("backdrop.jpg", m.get("backdrop_path")),
                   ("logo.png", pick_logo(images))]
    for rel, path in targets:
        if not path:
            print(f"  none on TMDB: {rel}")
            continue
        if download(IMG + path, root / rel, args.force):
            got.append(rel)
            print(f"  saved {rel}")
        else:
            print(f"  exists {rel}")
    print(f"{len(got)} images saved.")


# ----------------------------------------------------------------------------- nfo

def xml_file(tag: str, fields: list[tuple], dest: Path, force: bool) -> bool:
    if dest.exists() and not force:
        return False
    el = ET.Element(tag)
    for item in fields:
        name, value = item[0], item[1]
        if value in (None, "", []):
            continue
        child = ET.SubElement(el, name, **(item[2] if len(item) > 2 else {}))
        child.text = str(value)
    ET.indent(el)
    dest.write_text('<?xml version="1.0" encoding="utf-8" standalone="yes"?>\n'
                    + ET.tostring(el, encoding="unicode") + "\n", encoding="utf-8")
    return True


def ids(tmdb_id, ext: dict) -> list[tuple]:
    out = [("uniqueid", tmdb_id, {"type": "tmdb", "default": "true"})]
    if ext.get("imdb_id"):
        out.append(("uniqueid", ext["imdb_id"], {"type": "imdb"}))
    if ext.get("tvdb_id"):
        out.append(("uniqueid", ext["tvdb_id"], {"type": "tvdb"}))
    return out


def cmd_nfo(args):
    root = Path(args.dir).resolve()
    written = 0
    if args.tv:
        d = tmdb_show(args.tv)
        ext = d.get("external_ids") or {}
        fields = [("title", d.get("name")), ("originaltitle", d.get("original_name")), ("plot", d.get("overview")),
                  ("year", year_of(d.get("first_air_date"))), ("premiered", d.get("first_air_date")),
                  ("status", d.get("status"))]
        fields += [("genre", g["name"]) for g in d.get("genres", [])]
        fields += [("studio", n["name"]) for n in d.get("networks", [])]
        fields += ids(args.tv, ext)
        fields += [("tmdbid", args.tv), ("imdb_id", ext.get("imdb_id")), ("tvdbid", ext.get("tvdb_id"))]
        written += xml_file("tvshow", fields, root / "tvshow.nfo", args.force)
        seasons = {s["season_number"]: s for s in d["seasons_full"]}
        for s in seasons.values():
            folder = root / f"Season {s['season_number']:02d}"
            if folder.is_dir():
                written += xml_file("season", [("title", s["name"]), ("seasonnumber", s["season_number"]),
                                               ("plot", s.get("overview")), ("premiered", s.get("air_date"))],
                                    folder / "season.nfo", args.force)
        for p, sn, e, end in episode_files(root):
            eps = {x["episode_number"]: x for x in seasons.get(sn, {}).get("episodes", [])}
            ep = eps.get(e)
            if not ep:
                print(f"  not on TMDB, skipped: {p.relative_to(root)}")
                continue
            title = ep["name"]
            if end and eps.get(end):
                title = f"{title} / {eps[end]['name']}"
            written += xml_file("episodedetails", [
                ("title", title), ("showtitle", d.get("name")), ("season", sn), ("episode", e),
                ("aired", ep.get("air_date")), ("plot", ep.get("overview")), ("runtime", ep.get("runtime")),
                ("uniqueid", ep.get("id"), {"type": "tmdb", "default": "true"})],
                p.with_suffix(".nfo"), args.force)
    else:
        m = tmdb_movie(args.movie)
        ext = m.get("external_ids") or {}
        fields = [("title", m.get("title")), ("originaltitle", m.get("original_title")), ("plot", m.get("overview")),
                  ("tagline", m.get("tagline")), ("year", year_of(m.get("release_date"))),
                  ("premiered", m.get("release_date")), ("runtime", m.get("runtime"))]
        fields += [("genre", g["name"]) for g in m.get("genres", [])]
        fields += [("studio", c["name"]) for c in m.get("production_companies", [])[:3]]
        fields += [("country", c["name"]) for c in m.get("production_countries", [])]
        fields += ids(args.movie, ext)
        fields += [("tmdbid", args.movie), ("imdbid", ext.get("imdb_id"))]
        written += xml_file("movie", fields, root / "movie.nfo", args.force)
    print(f"{written} .nfo files written. Existing files were kept; use --force to overwrite.")


# ----------------------------------------------------------------------------- subtitles

OS_API = "https://api.opensubtitles.com/api/v1"


def os_hash(path: Path) -> str | None:
    size = path.stat().st_size
    if size < 131072:
        return None
    h = size
    with open(path, "rb") as f:
        for offset in (0, size - 65536):
            f.seek(offset)
            h += sum(struct.unpack("<8192Q", f.read(65536)))
    return f"{h & 0xFFFFFFFFFFFFFFFF:016x}"


class OpenSubtitles:
    def __init__(self):
        if not CONF.get("OPENSUBTITLES_API_KEY"):
            die(f"no OPENSUBTITLES_API_KEY in {CONFIG_FILE}")
        self.base = OS_API
        self.headers = {"Api-Key": CONF["OPENSUBTITLES_API_KEY"], "User-Agent": CONF["OPENSUBTITLES_USER_AGENT"]}
        self.remaining = None
        self.login()

    def login(self):
        user, pw = CONF.get("OPENSUBTITLES_USERNAME"), CONF.get("OPENSUBTITLES_PASSWORD")
        if not user or not pw:
            print("OpenSubtitles: no username, anonymous limit is 5 downloads a day.")
            return
        cache = CACHE_DIR / "opensubtitles-token.json"
        key = hashlib.sha256(f"{user}:{CONF['OPENSUBTITLES_API_KEY']}".encode()).hexdigest()
        if cache.exists():
            c = json.loads(cache.read_text())
            if c.get("key") == key and c.get("expires", 0) > time.time():
                self.headers["Authorization"] = f"Bearer {c['token']}"
                self.base = c["base"]
                return
        r = http(f"{OS_API}/login", method="POST", headers=self.headers, body={"username": user, "password": pw})
        self.headers["Authorization"] = f"Bearer {r['token']}"
        if r.get("base_url"):
            self.base = f"https://{r['base_url']}/api/v1"
        self.remaining = (r.get("user") or {}).get("allowed_downloads")
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"key": key, "token": r["token"], "base": self.base,
                                     "expires": time.time() + 20 * 3600}))
        cache.chmod(0o600)

    def search(self, **params) -> list:
        params = {k: str(v).lower() for k, v in params.items() if v not in (None, "")}
        q = urllib.parse.urlencode(sorted(params.items()))
        time.sleep(0.3)
        return http(f"{self.base}/subtitles?{q}", headers=self.headers).get("data", [])

    def download(self, file_id: int) -> tuple[bytes, str]:
        time.sleep(0.3)
        r = http(f"{self.base}/download", method="POST", headers=self.headers, body={"file_id": file_id})
        self.remaining = r.get("remaining")
        return http(r["link"], raw=True, headers={"Accept": "*/*"}), r.get("file_name") or "sub.srt"


def best_sub(results: list, lang: str) -> dict | None:
    cands = [r["attributes"] for r in results if r["attributes"].get("language", "").lower().split("-")[0] == lang
             and r["attributes"].get("files")]
    cands = [c for c in cands if not c.get("foreign_parts_only")]
    cands.sort(key=lambda a: (bool(a.get("moviehash_match")),
                              not (a.get("ai_translated") or a.get("machine_translated")),
                              not a.get("hearing_impaired"),
                              bool(a.get("from_trusted")),
                              a.get("download_count", 0)), reverse=True)
    return cands[0] if cands else None


def cmd_subs(args):
    root = Path(args.dir).resolve()
    want = [x.strip() for x in (args.langs or CONF["SUB_LANGS"]).split(",") if x.strip()]
    jobs = []
    if args.tv:
        for p, s, e, _ in sorted(episode_files(root), key=lambda x: (x[1], x[2])):
            missing = [l for l in want if l not in sub_langs(p, not args.no_probe)]
            if missing:
                jobs.append((p, missing, {"parent_tmdb_id": args.tv, "season_number": s, "episode_number": e,
                                          "type": "episode"}))
    else:
        for p in sorted(root.iterdir()):
            if kind_of(p) == "video":
                missing = [l for l in want if l not in sub_langs(p, not args.no_probe)]
                if missing:
                    jobs.append((p, missing, {"tmdb_id": args.movie, "type": "movie"}))
    need = sum(len(m) for _, m, _ in jobs)
    print(f"{len(jobs)} videos need {need} subtitle files ({', '.join(want)}).")
    if not jobs:
        return
    if args.dry_run:
        for p, missing, _ in jobs:
            print(f"  {p.relative_to(root)}: {', '.join(missing)}")
        return
    client = OpenSubtitles()
    saved, not_found = 0, []
    for p, missing, params in jobs:
        results = client.search(languages=",".join(missing), moviehash=os_hash(p), **params)
        for lang in missing:
            pick = best_sub(results, lang)
            if not pick:
                not_found.append(f"{p.relative_to(root)} [{lang}]")
                continue
            if client.remaining is not None and client.remaining <= 0:
                print("Download quota used up. Run again after the reset.")
                break
            try:
                data, name = client.download(pick["files"][0]["file_id"])
            except RuntimeError as e:
                if "406" in str(e) or "quota" in str(e).lower():
                    print("Download quota used up. Run again after the reset.")
                    break
                raise
            ext = Path(name).suffix.lower() if Path(name).suffix.lower() in SUBS else ".srt"
            dest = p.with_name(f"{p.stem}.{lang}{ext}")
            dest.write_bytes(data)
            saved += 1
            match = "hash match" if pick.get("moviehash_match") else f"release: {pick.get('release', '?')}"
            print(f"  saved {dest.relative_to(root)} ({match})")
        else:
            continue
        break
    print(f"{saved} subtitles saved. Downloads left today: {client.remaining if client.remaining is not None else '?'}")
    for n in not_found:
        print(f"  not found: {n}")


# ----------------------------------------------------------------------------- cli

def cmd_config(_):
    def show(key):
        v = CONF.get(key)
        return "set" if v and ("KEY" in key or "TOKEN" in key or "PASSWORD" in key) else (v or "missing")
    print(f"config file: {CONFIG_FILE} ({'found' if CONFIG_FILE.exists() else 'not found'})")
    for key in ("TMDB_API_KEY", "TMDB_READ_TOKEN", "TMDB_LANGUAGE", "OPENSUBTITLES_API_KEY", "OPENSUBTITLES_USERNAME",
                "OPENSUBTITLES_PASSWORD", "SUB_LANGS"):
        print(f"  {key}: {show(key)}")
    print(f"  ffprobe: {shutil.which('ffprobe') or 'missing, embedded tracks will not be checked'}")


def cmd_scan(args):
    print(json.dumps(scan(Path(args.dir).resolve(), not args.no_probe), indent=1, ensure_ascii=False))


def cmd_tmdb(args):
    if args.what == "search":
        kind = "tv" if args.type == "tv" else "movie"
        params = {"query": args.query}
        if args.year:
            params["first_air_date_year" if kind == "tv" else "year"] = args.year
        res = tmdb(f"/search/{kind}", **params).get("results", [])[:10]
        out = [{"id": r["id"], "title": r.get("name") or r.get("title"),
                "original": r.get("original_name") or r.get("original_title"),
                "year": year_of(r.get("first_air_date") or r.get("release_date")),
                "country": r.get("origin_country"), "overview": (r.get("overview") or "")[:160]} for r in res]
    elif args.what == "show":
        out = show_summary(tmdb_show(args.id))
    elif args.what == "movie":
        m = tmdb_movie(args.id)
        out = {"id": m["id"], "title": m.get("title"), "original_title": m.get("original_title"),
               "year": year_of(m.get("release_date")), "runtime": m.get("runtime"),
               "imdb_id": (m.get("external_ids") or {}).get("imdb_id"),
               "canonical_folder": canonical_name(m.get("title"), year_of(m.get("release_date")), m["id"])}
    else:
        source = "imdb_id" if args.id.startswith("tt") else "tvdb_id"
        r = tmdb(f"/find/{args.id}", external_source=source)
        out = {k: [{"id": x["id"], "title": x.get("name") or x.get("title")} for x in v]
               for k, v in r.items() if v and k in ("movie_results", "tv_results")}
    print(json.dumps(out, indent=1, ensure_ascii=False))


def cmd_propose(args):
    root = Path(args.dir).resolve()
    plan = propose(root, args.tv, args.movie, args.season, args.offset, not args.no_probe)
    out = Path(args.out) if args.out else CACHE_DIR / "plans" / f"{root.name[:60]}-{dt.datetime.now():%Y%m%d-%H%M%S}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(plan, indent=2, ensure_ascii=False))
    print(f"plan: {out}")
    print(f"{len(plan['moves'])} moves, {len(plan['unmatched'])} unmatched, {len(plan['warnings'])} warnings.")
    print(f"Review it with: jf.py apply {out}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def target(p, required=True):
        g = p.add_mutually_exclusive_group(required=required)
        g.add_argument("--tv", type=int, metavar="TMDB_ID")
        g.add_argument("--movie", type=int, metavar="TMDB_ID")

    sub.add_parser("config").set_defaults(fn=cmd_config)
    p = sub.add_parser("scan")
    p.add_argument("dir")
    p.add_argument("--no-probe", action="store_true")
    p.set_defaults(fn=cmd_scan)

    p = sub.add_parser("tmdb")
    tsub = p.add_subparsers(dest="what", required=True)
    s = tsub.add_parser("search")
    s.add_argument("type", choices=["tv", "movie"])
    s.add_argument("query")
    s.add_argument("--year", type=int)
    for name in ("show", "movie"):
        s = tsub.add_parser(name)
        s.add_argument("id", type=int)
    s = tsub.add_parser("find", help="TMDB ID from an IMDb (tt...) or TVDB ID")
    s.add_argument("id")
    p.set_defaults(fn=cmd_tmdb)

    p = sub.add_parser("propose")
    p.add_argument("dir")
    target(p)
    p.add_argument("--season", type=int, help="season for files whose names carry no season")
    p.add_argument("--offset", type=int, default=0, help="subtract this from every guessed episode number")
    p.add_argument("--out")
    p.add_argument("--no-probe", action="store_true")
    p.set_defaults(fn=cmd_propose)

    p = sub.add_parser("apply")
    p.add_argument("plan")
    p.add_argument("--execute", action="store_true")
    p.set_defaults(fn=cmd_apply)

    p = sub.add_parser("undo")
    p.add_argument("log")
    p.set_defaults(fn=cmd_undo)

    for name, fn in (("verify", cmd_verify), ("artwork", cmd_artwork), ("nfo", cmd_nfo), ("subs", cmd_subs)):
        p = sub.add_parser(name)
        p.add_argument("dir")
        target(p)
        p.set_defaults(fn=fn)
        if name in ("artwork", "nfo"):
            p.add_argument("--force", action="store_true", help="overwrite existing files")
        if name in ("verify", "subs"):
            p.add_argument("--no-probe", action="store_true")
        if name == "artwork":
            p.add_argument("--episode-thumbs", action="store_true")
        if name == "subs":
            p.add_argument("--langs", help="comma list, default from SUB_LANGS")
            p.add_argument("--dry-run", action="store_true")

    args = ap.parse_args()
    try:
        args.fn(args)
    except RuntimeError as e:
        die(str(e))


if __name__ == "__main__":
    main()
