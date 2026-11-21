#!/usr/bin/env python3
"""Builds a standard VPM repository listing (public/index.json + public/index.html)
from GitHub Releases of all repositories declared in source.json.
"""

from __future__ import annotations

import hashlib
import html
import io
import json
import os
import pathlib
import re
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parent.parent
SOURCE_PATH = ROOT / "source.json"
OUT_DIR = ROOT / "public"


def http_get(url: str, accept: str = "application/vnd.github+json") -> bytes:
    headers = {
        "User-Agent": "moepus-vpm-builder",
        "Accept": accept,
    }
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token and "api.github.com" in url:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def fetch_releases(repo: str) -> list[dict[str, Any]]:
    url = f"https://api.github.com/repos/{repo}/releases?per_page=100"
    try:
        raw = http_get(url)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print(f"[warn] {repo}: repository or releases not found (404), skipping")
            return []
        raise
    return json.loads(raw.decode("utf-8"))


def parse_sha256sums(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        m = re.match(r"^\s*([0-9a-fA-F]{64})\s+\*?(.+?)\s*$", line)
        if m:
            out[m.group(2).strip()] = m.group(1).lower()
    return out


def semver_key(ver: str) -> tuple[int, int, int, int, str]:
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)(?:-(.+))?$", ver)
    if not m:
        return (0, 0, 0, 0, ver)
    pre = m.group(4)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)), 0 if pre else 1, pre or "")


def collect_packages(repos: list[str]) -> dict[str, dict[str, Any]]:
    packages: dict[str, dict[str, Any]] = {}
    for repo in repos:
        releases = fetch_releases(repo)
        for rel in releases:
            if rel.get("draft"):
                continue
            assets = rel.get("assets") or []
            zips = [a for a in assets if str(a.get("name", "")).endswith(".zip")]
            if not zips:
                continue
            pkg_asset = next((a for a in assets if a.get("name") == "package.json"), None)
            sha_asset = next((a for a in assets if a.get("name") == "SHA256SUMS.txt"), None)
            sha_map: dict[str, str] = {}
            if sha_asset:
                try:
                    sha_map = parse_sha256sums(
                        http_get(sha_asset["browser_download_url"], "application/octet-stream").decode("utf-8", "ignore")
                    )
                except Exception as exc:
                    print(f"[warn] failed reading SHA256SUMS.txt for {repo} {rel.get('tag_name')}: {exc}")

            for z in zips:
                zname = z["name"]
                zurl = z["browser_download_url"]
                manifest: dict[str, Any] | None = None
                zip_sha = sha_map.get(zname, "")

                if pkg_asset and len(zips) == 1:
                    try:
                        manifest = json.loads(
                            http_get(pkg_asset["browser_download_url"], "application/octet-stream").decode("utf-8")
                        )
                        if not zip_sha and isinstance(manifest.get("zipSHA256"), str):
                            zip_sha = manifest["zipSHA256"].lower()
                    except Exception as exc:
                        print(f"[warn] failed reading package.json asset for {zname}: {exc}")

                if manifest is None or not zip_sha:
                    zbytes = http_get(zurl, "application/octet-stream")
                    if not zip_sha:
                        zip_sha = hashlib.sha256(zbytes).hexdigest().lower()
                    if manifest is None:
                        with zipfile.ZipFile(io.BytesIO(zbytes)) as zf:
                            if "package.json" not in zf.namelist():
                                continue
                            manifest = json.loads(zf.read("package.json").decode("utf-8"))

                pkg_id = manifest.get("name")
                ver = manifest.get("version")
                if not pkg_id or not ver:
                    continue

                entry = dict(manifest)
                entry["url"] = zurl
                entry["zipSHA256"] = zip_sha
                entry.setdefault("vpmDependencies", {})

                pkg_bucket = packages.setdefault(pkg_id, {"versions": {}})
                pkg_bucket["versions"][ver] = entry
                print(f"[ok] {pkg_id}@{ver} ({repo} / {zname})")

    # Sort versions newest-first for clean output
    for pkg_id, bucket in packages.items():
        ordered = dict(
            sorted(bucket["versions"].items(), key=lambda kv: semver_key(kv[0]), reverse=True)
        )
        bucket["versions"] = ordered
    return dict(sorted(packages.items()))


def render_html(source: dict[str, Any], index: dict[str, Any]) -> str:
    repo_url = source["url"]
    vcc_link = "vcc://vpm/addRepo?url=" + urllib.parse.quote(repo_url, safe="")
    title = html.escape(source.get("name", "VPM Repository"))
    desc = html.escape(source.get("description", ""))
    author = source.get("author", {})
    author_name = html.escape(author.get("name", "") if isinstance(author, dict) else str(author))
    author_url = html.escape(author.get("url", "#") if isinstance(author, dict) else "#")

    cards: list[str] = []
    for pkg_id, bucket in index.get("packages", {}).items():
        versions = bucket.get("versions", {})
        if not versions:
            continue
        latest_ver = next(iter(versions.keys()))
        latest = versions[latest_ver]
        disp = html.escape(latest.get("displayName", pkg_id))
        pdesc = html.escape(latest.get("description", ""))
        unity = html.escape(latest.get("unity", ""))
        dl_url = html.escape(latest.get("url", "#"))
        ver_links = " ".join(
            f'<a class="ver-pill" href="{html.escape(v.get("url", "#"))}">v{html.escape(k)}</a>'
            for k, v in list(versions.items())[:8]
        )
        cards.append(
            f"""
            <article class="card">
              <div class="card-head">
                <div>
                  <h2>{disp}</h2>
                  <div class="pkg-id">{html.escape(pkg_id)}{f' · Unity {unity}' if unity else ''}</div>
                </div>
                <a class="btn btn-secondary" href="{dl_url}">Download v{html.escape(latest_ver)}</a>
              </div>
              <p class="pkg-desc">{pdesc}</p>
              <div class="versions">{ver_links}</div>
            </article>
            """
        )

    if not cards:
        cards.append(
            """
            <article class="card">
              <h2>No published releases yet</h2>
              <p class="pkg-desc">Packages will appear here automatically as soon as the first GitHub Release is published.</p>
            </article>
            """
        )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title}</title>
  <style>
    :root {{ color-scheme: light dark; --bg: #0f1117; --card: #171a23; --border: #2a3040; --fg: #e6eaf2; --muted: #9aa4b8; --accent: #4f8cff; }}
    @media (prefers-color-scheme: light) {{ :root {{ --bg: #f6f8fb; --card: #ffffff; --border: #dce2ec; --fg: #141923; --muted: #5a6478; --accent: #2563eb; }} }}
    body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: var(--bg); color: var(--fg); line-height: 1.5; }}
    .wrap {{ max-width: 840px; margin: 0 auto; padding: 40px 20px 64px; }}
    h1 {{ margin: 0 0 8px; font-size: 2rem; }}
    .lead {{ margin: 0 0 24px; color: var(--muted); }}
    .actions {{ display: flex; flex-wrap: wrap; gap: 10px; align-items: center; margin-bottom: 32px; }}
    .btn {{ display: inline-block; padding: 10px 18px; border-radius: 8px; text-decoration: none; font-weight: 600; font-size: 0.95rem; cursor: pointer; border: 1px solid transparent; }}
    .btn-primary {{ background: var(--accent); color: #fff; }}
    .btn-secondary {{ background: transparent; color: var(--fg); border-color: var(--border); }}
    .url-box {{ font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: 0.85rem; padding: 9px 12px; border-radius: 8px; border: 1px solid var(--border); background: var(--card); color: var(--fg); }}
    .card {{ background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 20px; margin-bottom: 16px; }}
    .card-head {{ display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; flex-wrap: wrap; }}
    .card h2 {{ margin: 0; font-size: 1.25rem; }}
    .pkg-id {{ font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: 0.82rem; color: var(--muted); margin-top: 2px; }}
    .pkg-desc {{ margin: 12px 0; color: var(--fg); }}
    .versions {{ display: flex; flex-wrap: wrap; gap: 6px; }}
    .ver-pill {{ font-size: 0.8rem; padding: 3px 9px; border-radius: 999px; border: 1px solid var(--border); color: var(--muted); text-decoration: none; }}
    .ver-pill:hover {{ border-color: var(--accent); color: var(--accent); }}
    footer {{ margin-top: 36px; font-size: 0.85rem; color: var(--muted); }}
    footer a {{ color: var(--accent); }}
  </style>
</head>
<body>
  <main class="wrap">
    <h1>{title}</h1>
    <p class="lead">{desc}</p>
    <div class="actions">
      <a class="btn btn-primary" href="{html.escape(vcc_link)}">Add to VCC / ALCOM</a>
      <code class="url-box" id="repoUrl">{html.escape(repo_url)}</code>
      <button class="btn btn-secondary" onclick="navigator.clipboard.writeText(document.getElementById('repoUrl').textContent); this.textContent='Copied!';">Copy URL</button>
    </div>
    {''.join(cards)}
    <footer>
      Maintained by <a href="{author_url}">{author_name}</a> ·
      <a href="index.json">index.json</a>
    </footer>
  </main>
</body>
</html>
"""


def main() -> None:
    source = json.loads(SOURCE_PATH.read_text(encoding="utf-8"))
    packages = collect_packages(source.get("githubRepos", []))
    author = source.get("author", "MoePus")
    index = {
        "name": source["name"],
        "id": source["id"],
        "url": source["url"],
        "author": author.get("name", "MoePus") if isinstance(author, dict) else str(author),
        "packages": packages,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "index.json").write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (OUT_DIR / "index.html").write_text(render_html(source, index), encoding="utf-8")
    print(f"Generated {OUT_DIR / 'index.json'} ({len(packages)} package(s)) and index.html")


if __name__ == "__main__":
    main()
