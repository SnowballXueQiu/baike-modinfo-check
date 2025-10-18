#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mcmod_env_checker.py

功能：
- 扫描指定目录下的模组 JAR 文件
- 读取 JAR 内的 META-INF/mods.toml（或 neoforge.mods.toml），提取 [[mods]] 的 modId/displayName/version
-（可选）读取 Fabric 模组的 fabric.mod.json 提取 id/name/version
- 调用 MC百科搜索 https://search.mcmod.cn/s?key=<modId>，解析第一个结果的详情页链接
- 解析详情页的 “运行环境” 和 “运作方式（Forge/Fabric/NeoForge）”，并将其分类为 client/server/both/unknown
- 输出结构化 JSON 结果

依赖：requests、beautifulsoup4（若未安装，将自动使用标准库回退，但解析鲁棒性会略差）
"""

import os
import sys
import re
import json
import zipfile
import html as html_module
import urllib.parse

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)
TIMEOUT = 15
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_MODS_DIR = os.path.join(BASE_DIR, "mods")

# 可选依赖
try:
    import requests  # type: ignore
except Exception:
    requests = None

try:
    from bs4 import BeautifulSoup  # type: ignore
except Exception:
    BeautifulSoup = None


def read_text_from_zip(zf: zipfile.ZipFile, path: str) -> str | None:
    try:
        with zf.open(path) as f:
            return f.read().decode("utf-8", errors="ignore")
    except KeyError:
        return None
    except Exception:
        return None


def extract_mods_from_toml(toml_text: str | None) -> list[dict]:
    if not toml_text:
        return []
    blocks = re.findall(
        r"(?ms)^\s*\[\[mods\]\]\s*(.*?)(?=^\s*\[\[|\Z)", toml_text
    )
    results: list[dict] = []

    def parse_field(section: str, name: str) -> str | None:
        m = re.search(
            rf"(?m)^\s*{re.escape(name)}\s*=\s*(?:\"([^\"]*)\"|'([^']*)')",
            section,
        )
        if not m:
            return None
        return (m.group(1) or m.group(2) or "").strip()

    for sec in blocks:
        mod_id = parse_field(sec, "modId") or parse_field(sec, "modid")
        display_name = parse_field(sec, "displayName")
        version = parse_field(sec, "version")
        if mod_id:
            results.append(
                {
                    "modId": mod_id,
                    "displayName": display_name or "",
                    "version": version or "",
                    "source": "mods.toml",
                }
            )
    return results


def extract_fabric_mod(zf: zipfile.ZipFile) -> list[dict]:
    try:
        with zf.open("fabric.mod.json") as f:
            data = json.loads(f.read().decode("utf-8", errors="ignore"))
            mod_id = data.get("id") or (data.get("custom", {}) or {}).get("modId")
            display_name = data.get("name") or ""
            version = data.get("version") or ""
            if mod_id:
                return [
                    {
                        "modId": str(mod_id),
                        "displayName": str(display_name),
                        "version": str(version),
                        "source": "fabric.mod.json",
                    }
                ]
    except KeyError:
        pass
    except Exception:
        pass
    return []


def list_mod_entries_from_jar(jar_path: str) -> list[dict]:
    entries: list[dict] = []
    try:
        with zipfile.ZipFile(jar_path, "r") as zf:
            toml = (
                read_text_from_zip(zf, "META-INF/mods.toml")
                or read_text_from_zip(zf, "META-INF/neoforge.mods.toml")
            )
            entries.extend(extract_mods_from_toml(toml))
            if not entries:
                entries.extend(extract_fabric_mod(zf))
    except zipfile.BadZipFile:
        pass
    except Exception:
        pass
    # 附加jar信息
    for e in entries:
        e["jar"] = os.path.basename(jar_path)
    return entries


# 网络层

def fetch_html(url: str) -> str | None:
    if requests is not None:
        try:
            resp = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT)
            if resp.status_code == 200:
                return resp.text
            return None
        except Exception:
            return None
    # 回退 urllib
    try:
        import urllib.request

        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.read().decode("utf-8", errors="ignore")
    except Exception:
        return None


# 解析搜索页

def select_first_class_link_from_search(html: str, prefer_texts: list[str]) -> str | None:
    # 使用bs4更稳
    if BeautifulSoup is not None:
        soup = BeautifulSoup(html, "html.parser")
        items = soup.select("div.search-result-list div.result-item")
        candidates: list[tuple[str, str]] = []  # (href, text)
        for it in items:
            head_a_list = it.select("div.head a[href]")
            for a in head_a_list:
                href = (a.get("href") or "").strip()
                text = a.get_text(strip=True)
                if "/class/" in href:
                    candidates.append((href, text))
        # 优先匹配文本包含 prefer_texts
        def normalize(s: str) -> str:
            return (s or "").lower().replace(" ", "")

        prefers = [normalize(p) for p in prefer_texts if p]
        for href, text in candidates:
            nt = normalize(text)
            if any(p and p in nt for p in prefers):
                return href
        # 否则取第一个
        return candidates[0][0] if candidates else None
    # 简易回退：正则粗略提取
    mlist = re.findall(
        r'<div class="result-item".*?<div class="head">(.*?)</div>', html, re.S
    )
    for head_html in mlist:
        # 找第一个包含 /class/ 的 href
        mhref = re.search(r'href=\"(https?://[^\"]*\/class\/[^\"]*)\"', head_html)
        if mhref:
            return mhref.group(1)
    return None


# 解析详情页

def parse_class_page(html: str) -> dict:
    env_text = None
    loaders: list[str] = []
    if BeautifulSoup is not None:
        soup = BeautifulSoup(html, "html.parser")
        info = soup.select_one("div.class-info")
        if info:
            lis = info.select("div.class-info-left ul li")
            for li in lis:
                txt = li.get_text(" ", strip=True)
                if "运行环境" in txt:
                    env_text = txt.split("运行环境:")[-1].strip()
                if "运作方式" in txt:
                    loaders = [a.get_text(strip=True) for a in li.select("a")]
    else:
        # 回退：正则尝试提取
        m_env_block = re.search(
            r'<div class="class-info".*?</div>', html, re.S
        )
        if m_env_block:
            block = m_env_block.group(0)
            m_env_li = re.search(
                r'(<li[^>]*>[^<]*运行环境:\s*.*?</li>)', block, re.S
            )
            if m_env_li:
                env_li = m_env_li.group(1)
                env_text = re.sub(r"<[^>]+>", " ", env_li)
                env_text = env_text.split("运行环境:")[-1].strip()
            m_loader_li = re.search(r'(<li[^>]*>[^<]*运作方式:.*?</li>)', block, re.S)
            if m_loader_li:
                loader_li = m_loader_li.group(1)
                loaders = re.findall(r">\s*([A-Za-z]+)\s*<", loader_li)
    return {"env_text": env_text, "loaders": loaders}


# 分类

def classify_environment(env_text: str | None) -> str:
    if not env_text:
        return "unknown"
    t = env_text.replace(" ", "")
    # 常见模式
    both_required = ("客户端需装" in t and "服务端需装" in t) or ("通用" in t and "需装" in t)
    if both_required:
        return "both"
    if "客户端需装" in t:
        if "服务端可选" in t or "服务端可不装" in t:
            return "client"
        return "client"
    if "服务端需装" in t:
        if "客户端可选" in t or "客户端可不装" in t:
            return "server"
        return "server"
    # 可选都可装
    if "客户端可选" in t and "服务端可选" in t:
        return "both"
    # 仅端
    if "仅客户端" in t or "客户端仅" in t:
        return "client"
    if "仅服务端" in t or "仅服务器端" in t:
        return "server"
    # 通用 -> 多半 both
    if "通用" in t:
        return "both"
    return "both" if ("客户端" in t and "服务端" in t) else "unknown"


def search_and_parse_env(mod_id: str, display_name: str | None = None) -> dict:
    key = mod_id.strip()
    prefer_texts = [mod_id, display_name or ""]
    search_url = f"https://search.mcmod.cn/s?key={urllib.parse.quote_plus(key)}"
    search_html = fetch_html(search_url)
    if not search_html:
        return {
            "class_url": None,
            "env_text": None,
            "loaders": [],
            "classification": "unknown",
            "search_url": search_url,
        }
    class_href = select_first_class_link_from_search(search_html, prefer_texts)
    if not class_href:
        return {
            "class_url": None,
            "env_text": None,
            "loaders": [],
            "classification": "unknown",
            "search_url": search_url,
        }
    # 详情页
    class_url = class_href.strip().strip("\"'").strip()
    class_html = fetch_html(class_url)
    parsed = parse_class_page(class_html or "") if class_html else {"env_text": None, "loaders": []}
    classification = classify_environment(parsed.get("env_text"))
    return {
        "class_url": class_url,
        "env_text": parsed.get("env_text"),
        "loaders": parsed.get("loaders") or [],
        "classification": classification,
        "search_url": search_url,
    }


def scan_mods_directory(mods_dir: str) -> list[dict]:
    results: list[dict] = []
    if not os.path.isdir(mods_dir):
        return results
    for name in sorted(os.listdir(mods_dir)):
        if not name.lower().endswith(".jar"):
            continue
        jar_path = os.path.join(mods_dir, name)
        entries = list_mod_entries_from_jar(jar_path)
        if not entries:
            results.append(
                {
                    "jar": name,
                    "modId": None,
                    "displayName": None,
                    "version": None,
                    "env_text": None,
                    "classification": "unknown",
                    "loaders": [],
                    "class_url": None,
                    "search_url": None,
                    "note": "未识别到mods.toml或fabric.mod.json",
                }
            )
            continue
        for e in entries:
            info = search_and_parse_env(e.get("modId", ""), e.get("displayName"))
            results.append(
                {
                    "jar": name,
                    "modId": e.get("modId"),
                    "displayName": e.get("displayName") or None,
                    "version": e.get("version") or None,
                    "env_text": info.get("env_text"),
                    "classification": info.get("classification"),
                    "loaders": info.get("loaders") or [],
                    "class_url": info.get("class_url"),
                    "search_url": info.get("search_url"),
                    "source": e.get("source"),
                }
            )
    return results


def main():
    import argparse

    parser = argparse.ArgumentParser(description="MC百科运行环境分类器")
    parser.add_argument(
        "mods_dir",
        nargs="?",
        default=DEFAULT_MODS_DIR,
        help="模组目录（默认：项目下mods）",
    )
    parser.add_argument(
        "--json", dest="as_json", action="store_true", help="仅输出JSON"
    )
    args = parser.parse_args()

    mods_dir = os.path.abspath(args.mods_dir)
    results = scan_mods_directory(mods_dir)

    print(json.dumps(results, ensure_ascii=False, indent=2))
    if not args.as_json:
        # 额外打印简表
        print("\n== 摘要 ==")
        for r in results:
            print(
                f"{r.get('jar')} | {r.get('modId')} | {r.get('displayName')} | "
                f"{','.join(r.get('loaders') or [])} | {r.get('classification')} | "
                f"{(r.get('env_text') or '').strip()}"
            )


if __name__ == "__main__":
    main()