#!/usr/bin/env python3
"""ui-climb helper: screenshots, free checks, and a blind pairwise judge.

Standard library only. Needs a Chromium-family browser on PATH (or
UI_CLIMB_BROWSER), and for `judge` the `codex` or `claude` CLI.

Commands:
  shoot  <page> --out DIR       screenshots + metrics.json for one version
  check  <DIR> [--baseline DIR] apply the free gates to DIR/metrics.json
  judge  --a DIR --b DIR ...    ask the other model which version is better
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

BROWSERS = ("chromium", "chromium-browser", "google-chrome-stable", "google-chrome", "chrome")
DEFAULT_VIEWPORTS = "desktop=1280x1800,phone=390x1600"

# Runs inside the harness page against the iframe holding the target page.
METRICS_JS = r"""
function lum(c){const m=c.match(/[\d.]+/g);if(!m)return null;const a=m.length>3?+m[3]:1;
 if(a===0)return null;return m.slice(0,3).map(v=>{v/=255;return v<=.03928?v/12.92:Math.pow((v+.055)/1.055,2.4)})
 .reduce((s,v,i)=>s+v*[.2126,.7152,.0722][i],0)}
function bgOf(el,w){for(let e=el;e;e=e.parentElement){const s=w.getComputedStyle(e);
 if(s.backgroundImage&&s.backgroundImage!=='none')return null;const l=lum(s.backgroundColor);if(l!==null)return l}return 1}
function measure(w){const d=w.document,out={viewport_width:w.innerWidth,
 overflow_px:Math.max(0,d.documentElement.scrollWidth-d.documentElement.clientWidth),
 page_height:d.documentElement.scrollHeight,text_elements:0,small_text_count:0,low_contrast_count:0,small_text:[],low_contrast:[],
 images_without_alt:[...d.images].filter(i=>!i.hasAttribute('alt')).length};
 const seen=new Set();
 for(const el of d.body.querySelectorAll('*')){
  const own=[...el.childNodes].some(n=>n.nodeType===3&&n.textContent.trim());if(!own)continue;
  const s=w.getComputedStyle(el);if(s.display==='none'||s.visibility==='hidden'||+s.opacity===0)continue;
  const r=el.getBoundingClientRect();if(!r.width||!r.height)continue;
  out.text_elements++;const text=el.textContent.trim().slice(0,60);const fs=parseFloat(s.fontSize);
  if(fs<12){out.small_text_count++;if(out.small_text.length<20)out.small_text.push({tag:el.tagName.toLowerCase(),px:fs,text})}
  const fg=lum(s.color),bg=bgOf(el,w);if(fg===null||bg===null)continue;
  const ratio=(Math.max(fg,bg)+.05)/(Math.min(fg,bg)+.05);
  const large=fs>=24||(fs>=18.66&&+s.fontWeight>=700);
  const key=s.color+'|'+fs;
  if(ratio<(large?3:4.5)&&!seen.has(key)){seen.add(key);out.low_contrast_count++;if(out.low_contrast.length<20)
   out.low_contrast.push({tag:el.tagName.toLowerCase(),ratio:+ratio.toFixed(2),color:s.color,px:fs,text})}}
 return out}
"""


def die(msg: str, code: int = 2) -> None:
    print(f"ui-climb: {msg}", file=sys.stderr)
    sys.exit(code)


def browser() -> str:
    env = os.environ.get("UI_CLIMB_BROWSER")
    if env:
        return env
    for name in BROWSERS:
        if shutil.which(name):
            return name
    die("no Chromium-family browser found; set UI_CLIMB_BROWSER")
    return ""


def parse_viewports(spec: str) -> list[tuple[str, int, int]]:
    out = []
    for part in spec.split(","):
        name, _, size = part.strip().partition("=")
        m = re.fullmatch(r"(\d+)x(\d+)", size)
        if not name or not m:
            die(f"bad viewport '{part}', expected name=WIDTHxHEIGHT")
        out.append((name, int(m[1]), int(m[2])))
    return out


def harness(target: Path, width: int, height: int, with_metrics: bool) -> str:
    # The iframe gives the page a true narrow viewport; a headless window cannot go below ~500px.
    script = ""
    if with_metrics:
        script = ("<script>" + METRICS_JS + "f.onload=()=>{let r;try{r=measure(f.contentWindow)}"
                  "catch(e){r={error:String(e)}}const p=document.createElement('pre');p.id='ui-climb-metrics';"
                  "p.textContent=JSON.stringify(r);document.body.appendChild(p)}</script>")
    return (f'<!doctype html><html><body style="margin:0;overflow:hidden">'
            f'<iframe id="f" src="{target.as_uri()}" style="border:0;display:block;'
            f'width:{width}px;height:{height}px"></iframe>{script}</body></html>')


def run_browser(args: list[str]) -> subprocess.CompletedProcess:
    base = [browser(), "--headless", "--disable-gpu", "--hide-scrollbars",
            "--allow-file-access-from-files", "--virtual-time-budget=3000", "--no-first-run"]
    return subprocess.run(base + args, capture_output=True, text=True, timeout=120)


def cmd_shoot(a: argparse.Namespace) -> None:
    page = Path(a.page).resolve()
    if not page.is_file():
        die(f"page not found: {page}")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    metrics = {"page": str(page), "taken_at": dt.datetime.now().isoformat(timespec="seconds"), "viewports": {}}
    with tempfile.TemporaryDirectory(prefix="ui-climb-") as tmp:
        for name, w, h in parse_viewports(a.viewports):
            hp = Path(tmp) / f"{name}.html"
            hp.write_text(harness(page, w, h, with_metrics=False), encoding="utf-8")
            png = out / f"{name}.png"
            run_browser([f"--window-size={w},{h}", f"--screenshot={png}", hp.as_uri()])
            if not png.is_file():
                die(f"screenshot failed for {name}")
            hp.write_text(harness(page, w, h, with_metrics=True), encoding="utf-8")
            dom = run_browser([f"--window-size={max(w, 800)},{h}", "--dump-dom", hp.as_uri()]).stdout
            m = re.search(r'<pre id="ui-climb-metrics">(.*?)</pre>', dom, re.S)
            if not m:
                die(f"metrics failed for {name}")
            import html as _html
            metrics["viewports"][name] = json.loads(_html.unescape(m[1]))
            print(f"{name}: {png}")
    (out / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"metrics: {out / 'metrics.json'}")


def load_metrics(d: str) -> dict:
    path = Path(d) / "metrics.json"
    if not path.is_file():
        die(f"no metrics.json in {d}; run shoot first")
    return json.loads(path.read_text(encoding="utf-8"))


GATES = (("overflow_px", "horizontal overflow px"),
         ("low_contrast_count", "low-contrast text styles (color+size pairs)"),
         ("small_text_count", "text elements under 12px"))


def cmd_check(a: argparse.Namespace) -> None:
    cur = load_metrics(a.dir)
    base = load_metrics(a.baseline) if a.baseline else None
    failures, notes = [], []
    for name, v in cur["viewports"].items():
        if "error" in v:
            failures.append(f"{name}: metrics error {v['error']}")
            continue
        bv = (base or {}).get("viewports", {}).get(name, {})
        for key, label in GATES:
            limit = bv.get(key, 0) if base else 0
            if v[key] > limit:
                failures.append(f"{name}: {v[key]} {label} (limit {limit})")
            elif base and v[key] < bv.get(key, 0):
                notes.append(f"{name}: {label} improved {bv[key]} -> {v[key]}")
        if v["low_contrast"] and v["low_contrast_count"] > (bv.get("low_contrast_count", 0) if base else 0):
            notes.append(f"{name}: low-contrast samples: " +
                         "; ".join(f"{x['ratio']}:1 {x['color']} {x['px']}px '{x['text'][:30]}'" for x in v["low_contrast"][:5]))
        if v["images_without_alt"]:
            notes.append(f"{name}: {v['images_without_alt']} images without alt")
    result = {"pass": not failures, "mode": "no-worse-than-baseline" if base else "absolute",
              "failures": failures, "notes": notes}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result["pass"] else 1)


JUDGE_PROMPT = """You are reviewing two versions, A and B, of the same user interface.
Both show the same content; only the presentation differs. You do not know
which one is older, and neither is preferred.

Who uses it and what for, plus what matters (the rubric):
---
{rubric}
---

Images, in attachment order:
{images}

Compare A and B against every rubric point. Judge only what you can see.
Reply with ONLY one JSON object, no prose around it:
{{"winner": "A" | "B" | "tie",
  "confidence": "low" | "medium" | "high",
  "per_criterion": [{{"criterion": "...", "better": "A" | "B" | "tie", "why": "..."}}],
  "fixes_for_loser": ["concrete, visible change", "..."],
  "fixes_for_winner": ["the 3 changes that would most improve the better version"]}}
"""


def labelled_images(dirs: dict[str, Path], tmp: Path) -> list[tuple[str, Path]]:
    imgs = []
    for label in ("A", "B"):
        pngs = sorted(dirs[label].glob("*.png"))
        if not pngs:
            die(f"no screenshots in {dirs[label]}")
        for png in pngs:
            dst = tmp / f"{label}-{png.stem}.png"
            shutil.copy(png, dst)
            imgs.append((f"{label} ({png.stem})", dst))
    return imgs


def call_judge(judge: str, prompt: str, imgs: list[tuple[str, Path]], tmp: Path, model: str | None,
               effort: str | None) -> str:
    if judge == "codex":
        last = tmp / "last.txt"
        cmd = ["codex", "exec", "--sandbox", "read-only", "--skip-git-repo-check", "--ephemeral",
               "-C", str(tmp), "-o", str(last)]
        if model:
            cmd += ["-m", model]
        if effort:
            cmd += ["-c", f"model_reasoning_effort={effort}"]
        for _, p in imgs:
            cmd.append(f"--image={p}")
        # -i takes several values, so a positional prompt would be read as an image; send it on stdin.
        r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=1800)
        if r.returncode != 0:
            die(f"codex failed: {r.stderr[-800:]}")
        return last.read_text(encoding="utf-8") if last.is_file() else r.stdout
    if judge == "claude":
        paths = "\n".join(f"- {label}: {p}" for label, p in imgs)
        cmd = ["claude", "-p", "--tools", "Read", "--allowedTools", "Read", "--add-dir", str(tmp),
               "--no-session-persistence"]
        if model:
            cmd += ["--model", model]
        if effort:
            cmd += ["--effort", effort]
        cmd.append(prompt + "\nRead each image file before judging:\n" + paths)
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800, cwd=tmp, stdin=subprocess.DEVNULL)
        if r.returncode != 0:
            die(f"claude failed: {r.stderr[-800:]}")
        return r.stdout
    die(f"unknown judge {judge}")
    return ""


def parse_verdict(text: str) -> dict:
    for m in re.finditer(r"\{", text):
        try:
            obj, _ = json.JSONDecoder().raw_decode(text[m.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("winner") in ("A", "B", "tie"):
            return obj
    die("judge reply had no verdict JSON:\n" + text[-1500:])
    return {}


def one_pass(a: argparse.Namespace, first: str, second: str, rubric: str) -> tuple[str, dict]:
    """Returns the winner as 'first'/'second'/'tie' plus the raw verdict (labels mapped back)."""
    dirs = {"A": Path(first), "B": Path(second)}
    with tempfile.TemporaryDirectory(prefix="ui-climb-judge-") as t:
        tmp = Path(t)
        imgs = labelled_images(dirs, tmp)
        listing = "\n".join(f"{i}. {label}" for i, (label, _) in enumerate(imgs, 1))
        verdict = parse_verdict(call_judge(a.judge, JUDGE_PROMPT.format(rubric=rubric, images=listing),
                                           imgs, tmp, a.model, a.effort))
    return {"A": "first", "B": "second", "tie": "tie"}[verdict["winner"]], verdict


def cmd_judge(a: argparse.Namespace) -> None:
    rubric = Path(a.rubric).read_text(encoding="utf-8")
    versions = {"a": a.a, "b": a.b}
    order = ["a", "b"]
    random.shuffle(order)  # position bias: the judge never knows which is the candidate
    passes = []
    for o in ([order, order[::-1]] if a.both_orders else [order]):
        res, verdict = one_pass(a, versions[o[0]], versions[o[1]], rubric)
        winner = {"first": o[0], "second": o[1], "tie": "tie"}[res]
        passes.append({"shown_as_A": o[0], "winner": winner, "verdict": verdict})
    winners = {p["winner"] for p in passes}
    final = winners.pop() if len(winners) == 1 else "tie"
    record = {"at": dt.datetime.now().isoformat(timespec="seconds"), "judge": a.judge, "model": a.model, "effort": a.effort,
              "a": a.a, "b": a.b, "winner": final, "passes": passes}
    if a.ledger:
        with open(a.ledger, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(json.dumps(record, ensure_ascii=False, indent=2))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("shoot", help="screenshot a local HTML page and record metrics")
    s.add_argument("page", help="local .html file")
    s.add_argument("--out", required=True, help="directory for PNGs and metrics.json")
    s.add_argument("--viewports", default=DEFAULT_VIEWPORTS, help=f"default {DEFAULT_VIEWPORTS}")
    s.set_defaults(fn=cmd_shoot)

    c = sub.add_parser("check", help="apply free gates to a shoot directory; exit 1 on failure")
    c.add_argument("dir")
    c.add_argument("--baseline", help="shoot directory to compare against: fail only where this version is worse")
    c.set_defaults(fn=cmd_check)

    j = sub.add_parser("judge", help="blind pairwise comparison by the other model")
    j.add_argument("--a", required=True, help="shoot directory of version a (e.g. current best)")
    j.add_argument("--b", required=True, help="shoot directory of version b (e.g. candidate)")
    j.add_argument("--rubric", required=True, help="markdown file: users, tasks, criteria")
    j.add_argument("--judge", choices=("codex", "claude"), required=True)
    j.add_argument("--model", help="judge model override")
    j.add_argument("--effort", choices=("low", "medium", "high"), help="judge reasoning effort (cheaper when lower)")
    j.add_argument("--both-orders", action="store_true", help="judge twice with A/B swapped; disagreement = tie")
    j.add_argument("--ledger", help="append the result to this JSONL file")
    j.set_defaults(fn=cmd_judge)

    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
