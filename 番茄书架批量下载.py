#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
番茄书架批量下载器 v3 —— 多账号 + 多实例并发 + 进度显示 + 自动整理

功能:
  - 多账号管理(添加/切换/删除), 保存在 accounts.json
  - 启动时校验 Cookie, 失效则提示并可打开浏览器登录页
  - 多实例并发下载: 启动多个下载器实例, 书籍分配到各实例并行下载
  - 实时进度百分比显示
  - 下载后自动移动到桌面时间戳文件夹(如 2611092125)
  - 下载后自动清理数字缓存文件夹
  - 菜单:
      1  下载指定分组书籍(多实例并发 + 进度 + 自动整理)
      2  书籍移动分组(已下载书籍在本地分组间移动, 可按书名过滤)
      Z  账号设置
      0  退出

前置:
  1. 复制 Cookie 到 cookie.txt 或用账号设置添加
  2. 无需手动启动下载器, 脚本会自动启动多个实例
"""
import argparse
import gzip
import hashlib
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
import webbrowser
import shutil
import subprocess
from datetime import datetime

HOST = "https://fanqienovel.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
SHELF_PATH = "/reading/bookapi/bookshelf/info/v{ver}/"
SHELF_QUERY = "aid=1967&device_platform=web&app_name=muye_novel&version_code=9999"
CTX = ssl._create_unverified_context()
BOOKSHELF_URL = "https://fanqienovel.com/bookshelf?enter_from=menu"
ACCOUNTS_FILE = "accounts.json"
GROUPS_DIR = "下载分组"
EXE_NAME = "TomatoNovelDownloader.exe"
DESKTOP = os.path.join(os.path.expanduser("~"), "Desktop")


# ───────────────────────── 账号管理 ─────────────────────────

def load_accounts():
    if os.path.isfile(ACCOUNTS_FILE):
        try:
            with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"current": None, "accounts": {}}


def save_accounts(accounts):
    with open(ACCOUNTS_FILE, "w", encoding="utf-8") as f:
        json.dump(accounts, f, ensure_ascii=False, indent=2)


def get_current_cookie(accounts):
    name = accounts.get("current")
    if name and name in accounts.get("accounts", {}):
        return name, accounts["accounts"][name].get("cookie", "")
    return None, ""


# ───────────────────────── 番茄侧接口 ─────────────────────────

def fq_get_json(path, cookie, referer=None, timeout=30):
    url = HOST + path
    headers = {
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Cookie": cookie,
        "Accept-Encoding": "gzip",
    }
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=timeout, context=CTX)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")
        try:
            return json.loads(body)
        except Exception:
            return {"_http": e.code, "_body": body[:200]}
    raw = resp.read()
    if resp.headers.get("Content-Encoding") == "gzip":
        raw = gzip.decompress(raw)
    return json.loads(raw.decode("utf-8", "ignore"))


def fetch_shelf(cookie):
    for ver in ("0", "1", "2"):
        path = SHELF_PATH.format(ver=ver) + "?" + SHELF_QUERY
        data = fq_get_json(path, cookie, referer=HOST + "/bookshelf")
        if data.get("code") == 0 and isinstance(data.get("data"), dict):
            return data["data"]
    return None


def validate_cookie(cookie):
    if not cookie:
        return None
    return fetch_shelf(cookie)


def parse_shelf_groups(shelf_data):
    from collections import OrderedDict
    shelf = shelf_data.get("book_shelf_info") or []
    gmap = {}
    for it in shelf:
        gid = it.get("group_id")
        gname = it.get("group_name") or ""
        if gid is not None and gname and gid not in gmap:
            gmap[gid] = gname
    counter = OrderedDict()
    bookmap = OrderedDict()
    for it in shelf:
        gid = it.get("group_id")
        gname = gmap.get(gid, "") or it.get("group_name") or "(未分组)"
        counter[gname] = counter.get(gname, 0) + 1
        bookmap.setdefault(gname, []).append(str(it.get("book_id")))
    return counter, bookmap


def get_csrf_token(cookie):
    """从 cookie 提取 passport_csrf_token。"""
    for part in cookie.split(";"):
        part = part.strip()
        if part.startswith("passport_csrf_token="):
            return part.split("=", 1)[1]
    return ""


def fq_post_json(path, cookie, body, referer=None, timeout=30):
    """POST JSON 到番茄接口, 带 csrf token。"""
    url = HOST + path
    headers = {
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Cookie": cookie,
        "Content-Type": "application/json; charset=utf-8",
        "Accept-Encoding": "gzip",
    }
    csrf = get_csrf_token(cookie)
    if csrf:
        headers["X-Secsdk-Csrf-Token"] = csrf
    if referer:
        headers["Referer"] = referer
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST", headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=timeout, context=CTX)
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", "ignore")
        try:
            return json.loads(body_text)
        except Exception:
            return {"_http": e.code, "_body": body_text[:200]}
    raw = resp.read()
    if resp.headers.get("Content-Encoding") == "gzip":
        raw = gzip.decompress(raw)
    return json.loads(raw.decode("utf-8", "ignore"))


def fq_get_html(path, cookie, referer=None, timeout=30):
    """GET HTML 页面。"""
    url = HOST + path
    headers = {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml",
        "Cookie": cookie,
        "Accept-Encoding": "gzip",
    }
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=timeout, context=CTX)
    except urllib.error.HTTPError as e:
        return e.read().decode("utf-8", "ignore")
    raw = resp.read()
    if resp.headers.get("Content-Encoding") == "gzip":
        raw = gzip.decompress(raw)
    return raw.decode("utf-8", "ignore")


def get_book_tag(book_id, cookie):
    """获取书籍的标签(完结/连载/断更)。从书籍页面解析 info-label-yellow。"""
    import re
    for attempt in range(3):
        try:
            html = fq_get_html("/page/" + str(book_id), cookie, referer=HOST + "/bookshelf")
            m = re.search(r'<span[^>]*class="info-label-yellow"[^>]*>([^<]+)</span>', html)
            if m:
                label = m.group(1).strip()
                if "完结" in label:
                    return "完结"
                if "连载" in label:
                    return "连载"
                if "断更" in label:
                    return "断更"
                return label
        except Exception:
            pass
        time.sleep(0.5)
    return None


def move_books_to_shelf_group(book_infos, cookie):
    """把书籍移动到番茄书架的指定分组。
    book_infos: [{"book_id": "...", "group_name": "...", "modify_time": ...}, ...]
    注意: update 端点用 group_name 而非 group_id。
    """
    if not book_infos:
        return False, "参数为空"
    ok, fail = 0, 0
    for i in range(0, len(book_infos), 50):
        batch = book_infos[i:i + 50]
        body = {"book_data": [
            {"book_id": b["book_id"], "group_name": b["group_name"],
             "modify_time": b.get("modify_time")}
            for b in batch
        ]}
        path = "/reading/bookapi/bookshelf/update/v0/?" + SHELF_QUERY
        r = fq_post_json(path, cookie, body, referer=HOST + "/bookshelf")
        if r.get("code") == 0:
            err = (r.get("data") or {}).get("error_update_info")
            if err:
                fail += len(err)
                ok += len(batch) - len(err)
            else:
                ok += len(batch)
        else:
            fail += len(batch)
    return ok > 0, f"成功 {ok}, 失败 {fail}"


# ───────────────────────── 下载器实例池 ─────────────────────────

class DownloaderPool:
    """管理多个下载器实例, 实现并发下载。"""

    def __init__(self, base_dir, count, password=None):
        self.base_dir = base_dir
        self.count = count
        self.password = password
        self.instances = []  # [{"port":.., "dir":.., "proc":..}]

    def start(self):
        exe = os.path.join(self.base_dir, EXE_NAME)
        if not os.path.isfile(exe):
            print(f"    [!] 找不到 {EXE_NAME}")
            return False
        for i in range(self.count):
            port = 18423 + i
            inst_dir = os.path.join(self.base_dir, f"dl_inst_{i+1}")
            os.makedirs(inst_dir, exist_ok=True)
            # 复制 config.yml
            cfg = os.path.join(self.base_dir, "config.yml")
            if os.path.isfile(cfg):
                shutil.copy(cfg, os.path.join(inst_dir, "config.yml"))
            env = os.environ.copy()
            env["TOMATO_WEB_ADDR"] = f"127.0.0.1:{port}"
            try:
                proc = subprocess.Popen(
                    [exe, "--server"], cwd=inst_dir, env=env,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception as e:
                print(f"    [!] 实例{i+1}启动失败: {e}")
                return False
            self.instances.append({"port": port, "dir": inst_dir, "proc": proc})
        # 等待就绪
        for i, inst in enumerate(self.instances):
            for _ in range(20):
                if self._alive(inst["port"]):
                    break
                time.sleep(0.5)
        print(f"    已启动 {len(self.instances)} 个下载器实例")
        return True

    def _alive(self, port):
        try:
            r = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/jobs?all=true",
                headers={"Accept": "application/json"})
            urllib.request.urlopen(r, timeout=5).read()
            return True
        except Exception:
            return False

    def _req(self, port, path, method="GET", body=None):
        data = json.dumps(body).encode() if body is not None else None
        h = {"Accept": "application/json"}
        if self.password:
            h["x-tomato-password"] = hashlib.sha256(self.password.encode()).hexdigest()
        if data:
            h["Content-Type"] = "application/json"
        r = urllib.request.Request(f"http://127.0.0.1:{port}" + path,
                                   data=data, method=method, headers=h)
        try:
            resp = urllib.request.urlopen(r, timeout=60)
            return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read().decode())
            except Exception:
                return {"_http": e.code}
        except Exception as e:
            return {"_err": str(e)}

    def set_format(self, fmt):
        for inst in self.instances:
            self._req(inst["port"], "/api/config", "POST", {"novel_format": fmt})

    def submit(self, book_id):
        """提交到当前最空闲的实例(轮询)。返回 (实例索引, job_id) 或 (None, None)。"""
        for idx in range(len(self.instances)):
            inst = self.instances[idx]
            # 检查是否有活跃job
            j = self._req(inst["port"], "/api/jobs?all=true")
            active = [it for it in (j.get("items") or [])
                      if it.get("state") in ("queued", "running")]
            if not active:
                r = self._req(inst["port"], "/api/jobs", "POST", {"book_id": book_id})
                if "_http" not in r and "_err" not in r and r.get("id"):
                    return idx, r["id"]
        return None, None

    def poll_all(self):
        """轮询所有实例, 返回 {inst_idx: [job_info...]}。"""
        result = {}
        for idx, inst in enumerate(self.instances):
            j = self._req(inst["port"], "/api/jobs?all=true")
            result[idx] = j.get("items") or []
        return result

    def job_progress(self, port, job_id):
        j = self._req(port, f"/api/jobs?id={job_id}")
        items = j.get("items") or []
        if not items:
            return None
        it = items[0]
        p = it.get("progress") or {}
        return it.get("state"), p.get("saved_chapters", 0), p.get("chapter_total", 0)

    def shutdown(self):
        for inst in self.instances:
            try:
                inst["proc"].terminate()
            except Exception:
                pass


# ───────────────────────── 下载流程(多实例并发) ─────────────────────────

def download_group_books(pool, book_ids, fmt):
    """多实例并发下载一组书籍。返回 (ok, fail)。"""
    pool.set_format(fmt)
    # 提交所有书到实例池
    queue = list(enumerate(book_ids))  # (原序号, book_id)
    pending = []  # (原序号, book_id, inst_idx, job_id)
    total = len(queue)
    # 初始提交
    for idx, bid in queue:
        inst_idx, job_id = pool.submit(bid)
        if inst_idx is not None:
            pending.append([idx, bid, inst_idx, job_id])
        else:
            # 没有空闲实例, 稍后重试
            pass
    # 轮询进度
    ok, fail = 0, 0
    last_print = 0
    while pending:
        time.sleep(2)
        still = []
        progress_lines = []
        for p in pending:
            idx, bid, inst_idx, job_id = p
            inst = pool.instances[inst_idx]
            state, saved, total_ch = pool.job_progress(inst["port"], job_id)
            if state is None:
                still.append(p)
                continue
            if state == "done":
                ok += 1
                print(f"    [完成 {ok+fail}/{total}] {bid}")
            elif state == "failed":
                fail += 1
                print(f"    [失败 {ok+fail}/{total}] {bid}")
            else:
                pct = (saved / total_ch * 100) if total_ch else 0
                bar = "#" * int(pct // 5)
                progress_lines.append(f"    实例{inst_idx+1} {pct:5.1f}% [{bar:<20}] {saved}/{total_ch}")
                still.append(p)
        # 打印进度
        if progress_lines:
            print("\n".join(progress_lines))
        # 提交剩余未分配的书
        if len(pending) < len(queue) - ok - fail:
            for idx, bid in queue:
                if any(p[0] == idx for p in pending):
                    continue
                if any(p[1] == bid for p in pending):
                    continue
                inst_idx, job_id = pool.submit(bid)
                if inst_idx is not None:
                    pending.append([idx, bid, inst_idx, job_id])
        pending = still
    return ok, fail


def organize_to_desktop(pool):
    """下载完成后, 把书籍文件移动到桌面时间戳文件夹, 并清理缓存。"""
    now = datetime.now()
    ts = now.strftime("%y%m%d%H%M")
    target_dir = os.path.join(DESKTOP, ts)
    os.makedirs(target_dir, exist_ok=True)
    print(f"\n    移动书籍到桌面文件夹: {target_dir}")
    # 从各实例目录收集书籍文件
    moved = 0
    for inst in pool.instances:
        inst_dir = inst["dir"]
        exts = (".txt", ".epub", ".pdf")
        for f in os.listdir(inst_dir):
            if f.lower().endswith(exts):
                src = os.path.join(inst_dir, f)
                dst = os.path.join(target_dir, f)
                try:
                    shutil.move(src, dst)
                    moved += 1
                except Exception as e:
                    print(f"    [!] 移动失败 {f}: {e}")
    # 清理数字缓存文件夹
    print("\n    清理数字缓存文件夹...")
    cleaned = 0
    for inst in pool.instances:
        inst_dir = inst["dir"]
        for name in os.listdir(inst_dir):
            if name.isdigit():
                try:
                    shutil.rmtree(os.path.join(inst_dir, name), ignore_errors=True)
                    cleaned += 1
                except Exception:
                    pass
    print(f"    完成: 移动 {moved} 本到桌面, 清理 {cleaned} 个缓存文件夹")


# ───────────────────────── 本地分组管理 ─────────────────────────

def ensure_groups_dir():
    if not os.path.isdir(GROUPS_DIR):
        os.makedirs(GROUPS_DIR, exist_ok=True)


def list_local_groups():
    ensure_groups_dir()
    groups = []
    for name in os.listdir(GROUPS_DIR):
        p = os.path.join(GROUPS_DIR, name)
        if os.path.isdir(p):
            files = [f for f in os.listdir(p)
                     if f.lower().endswith((".txt", ".epub", ".pdf"))]
            groups.append((name, len(files)))
    return groups


def create_local_group(name):
    ensure_groups_dir()
    p = os.path.join(GROUPS_DIR, name)
    if not os.path.isdir(p):
        os.makedirs(p, exist_ok=True)
    return p


def move_book_to_group(book_file, target_group):
    ensure_groups_dir()
    target_dir = os.path.join(GROUPS_DIR, target_group)
    if not os.path.isdir(target_dir):
        create_local_group(target_group)
    dest = os.path.join(target_dir, os.path.basename(book_file))
    try:
        if os.path.abspath(book_file) == os.path.abspath(dest):
            return True, "已在目标分组"
        shutil.move(book_file, dest)
        return True, dest
    except Exception as e:
        return False, str(e)


def find_downloaded_books():
    exts = (".txt", ".epub", ".pdf")
    skip = {"cookie.txt", "ids_完本.txt", ACCOUNTS_FILE, "config.yml"}
    books = []
    for f in os.listdir("."):
        if f.lower().endswith(exts) and f not in skip:
            books.append(f)
    return books


# ───────────────────────── 浏览器登录 ─────────────────────────

def open_login_browser():
    print(f"\n    正在打开浏览器登录页...")
    print(f"    网址: {BOOKSHELF_URL}")
    try:
        webbrowser.open(BOOKSHELF_URL)
    except Exception as e:
        print(f"    [!] 自动打开失败: {e}, 请手动访问上面的网址")
    return True


def input_cookie_from_user():
    print("\n    请在浏览器登录 fanqienovel.com 后, 复制 Cookie:")
    print("    F12 -> Network -> 任一请求 -> Request Headers -> 复制整段 Cookie")
    try:
        cookie = input("    粘贴 Cookie (直接回车取消): ").strip()
    except EOFError:
        cookie = ""
    return cookie


# ───────────────────────── 功能: 下载分组 ─────────────────────────

def func_download(pool, fmt, shelf_counter, shelf_bookmap, workers):
    print("\n--- 功能1: 下载指定分组书籍 ---")
    for i, (gname, cnt) in enumerate(shelf_counter.items(), 1):
        print(f"    [{i}] {gname}  ({cnt} 本)")
    try:
        choice = input("    选择分组(数字): ").strip()
    except EOFError:
        choice = ""
    if not choice.isdigit() or not (1 <= int(choice) <= len(shelf_counter)):
        print("    输入无效, 返回。")
        return
    gname = list(shelf_counter.keys())[int(choice) - 1]
    books = shelf_bookmap.get(gname, [])
    if not books:
        print("    该分组没有书。")
        return
    print(f"\n    分组'{gname}'共 {len(books)} 本, 并发实例={workers}, 格式={fmt}")
    ok, fail = download_group_books(pool, books, fmt)
    print(f"\n    下载完成: 成功 {ok}, 失败 {fail}")
    # 自动整理到桌面
    organize_to_desktop(pool)
    # 询问是否移动到本地分组
    print("\n    是否把已下载书籍移动到本地分组?")
    try:
        ans = input("    [Y/n]: ").strip().lower()
    except EOFError:
        ans = ""
    if ans in ("y", "yes", ""):
        move_downloaded_books()


def move_downloaded_books():
    print("\n--- 移动已下载书籍到本地分组 ---")
    books = find_downloaded_books()
    if not books:
        print("    没有找到已下载的书籍文件。")
        return
    print(f"    找到 {len(books)} 个书籍文件:")
    for i, b in enumerate(books, 1):
        print(f"      {i}. {b}")
    groups = list_local_groups()
    if groups:
        print("\n    可用本地分组:")
        for i, (g, c) in enumerate(groups, 1):
            print(f"      {i}. {g}  ({c} 本)")
    try:
        gi = input("    选择目标分组(数字, 直接回车创建新分组): ").strip()
    except EOFError:
        gi = ""
    if gi.isdigit() and groups and 1 <= int(gi) <= len(groups):
        target = groups[int(gi) - 1][0]
    else:
        target = gi.strip() or "默认分组"
    create_local_group(target)
    ok = 0
    for b in books:
        s, msg = move_book_to_group(b, target)
        if s:
            ok += 1
    print(f"    完成: 移动 {ok} 本到分组'{target}'")


# ───────────────────────── 功能: 移动分组 ─────────────────────────

def func_move(cookie, shelf_data):
    print("\n--- 功能2: 书籍移动分组(按标签) ---")
    print("    (获取书架分组书籍的标签, 按标签移动到目标分组)")

    def refresh():
        nonlocal shelf_data, shelf, gmap, groups
        shelf_data = validate_cookie(cookie)
        if not shelf_data:
            print("    [!] 刷新书架失败, Cookie 可能失效。")
            return False
        shelf = shelf_data.get("book_shelf_info") or []
        gmap = {}
        for it in shelf:
            gid = it.get("group_id")
            gname = it.get("group_name") or ""
            if gid is not None and gname and gid not in gmap:
                gmap[gid] = gname
        groups = []
        seen = set()
        for it in shelf:
            gid = it.get("group_id")
            gname = gmap.get(gid, "") or it.get("group_name") or "(未分组)"
            if gname not in seen:
                seen.add(gname)
                groups.append((gname, gid))
        return True

    shelf = shelf_data.get("book_shelf_info") or []
    gmap = {}
    for it in shelf:
        gid = it.get("group_id")
        gname = it.get("group_name") or ""
        if gid is not None and gname and gid not in gmap:
            gmap[gid] = gname
    groups = []
    seen = set()
    for it in shelf:
        gid = it.get("group_id")
        gname = gmap.get(gid, "") or it.get("group_name") or "(未分组)"
        if gname not in seen:
            seen.add(gname)
            groups.append((gname, gid))

    while True:
        # 每轮重新拉取书架数据, 保证分组和数量是最新的
        if not refresh():
            return
        # 显示分组
        print("\n    书架分组:")
        for i, (gname, gid) in enumerate(groups, 1):
            cnt = sum(1 for it in shelf
                      if (gmap.get(it.get("group_id"), "") or it.get("group_name") or "(未分组)") == gname)
            print(f"      {i}. {gname}  ({cnt} 本)")
        print(f"      {len(groups)+1}. 返回上级菜单")
        try:
            si = input("    选择来源分组(数字): ").strip()
        except EOFError:
            si = str(len(groups) + 1)
        if si == str(len(groups) + 1):
            return
        if not si.isdigit() or not (1 <= int(si) <= len(groups)):
            print("    输入无效, 重新选择。")
            continue
        src_name = groups[int(si) - 1][0]

        # 获取该分组的书籍 + 标签
        src_books = [it for it in shelf
                     if (gmap.get(it.get("group_id"), "") or it.get("group_name") or "(未分组)") == src_name]
        if not src_books:
            print(f"    分组'{src_name}'没有书籍。")
            continue
        print(f"\n    获取分组'{src_name}'的 {len(src_books)} 本书的标签...")
        tagged = []
        for it in src_books:
            bid = str(it.get("book_id"))
            tag = get_book_tag(bid, cookie)
            tagged.append((bid, tag))
        from collections import Counter
        tag_counter = Counter(t for _, t in tagged)
        print("\n    标签统计:")
        tag_list = []
        for i, (tag, cnt) in enumerate(tag_counter.most_common(), 1):
            print(f"      {i}. {tag}: {cnt} 本")
            tag_list.append(tag)
        print(f"      {len(tag_list)+1}. 返回上级菜单")
        try:
            ti = input("    选择要移动的标签(数字): ").strip()
        except EOFError:
            ti = str(len(tag_list) + 1)
        if ti == str(len(tag_list) + 1):
            continue
        if not ti.isdigit() or not (1 <= int(ti) <= len(tag_list)):
            print("    输入无效, 重新选择。")
            continue
        target_tag = tag_list[int(ti) - 1]
        matched = [bid for bid, tag in tagged if tag == target_tag]
        if not matched:
            print(f"    没有'{target_tag}'标签的书籍。")
            continue
        print(f"\n    匹配 {len(matched)} 本'{target_tag}'标签书籍:")
        for bid in matched:
            print(f"      {bid}")
        # 目标分组
        print("\n    目标分组:")
        for i, (gname, gid) in enumerate(groups, 1):
            print(f"      {i}. {gname}")
        print(f"      {len(groups)+1}. 返回上级菜单")
        try:
            dti = input("    选择目标分组(数字): ").strip()
        except EOFError:
            dti = str(len(groups) + 1)
        if dti == str(len(groups) + 1):
            continue
        if not dti.isdigit() or not (1 <= int(dti) <= len(groups)):
            print("    输入无效, 重新选择。")
            continue
        dst_name, dst_gid = groups[int(dti) - 1]
        # 执行移动
        print(f"\n    移动 {len(matched)} 本'{target_tag}'书籍: {src_name} -> {dst_name}")
        # 构建 book_infos (带 group_name 和 modify_time)
        book_infos = []
        for it in src_books:
            if str(it.get("book_id")) in matched:
                book_infos.append({
                    "book_id": str(it.get("book_id")),
                    "group_name": dst_name,
                    "modify_time": it.get("modify_time"),
                })
        ok, msg = move_books_to_shelf_group(book_infos, cookie)
        print(f"    结果: {msg}")
        if ok:
            refresh()
        print("    (继续移动请再次选择, 输入返回上级菜单)")


# ───────────────────────── 功能Z: 账号设置 ─────────────────────────

def func_account_settings(accounts):
    print("\n--- 功能Z: 账号设置 ---")
    while True:
        print("    [1] 添加账号")
        print("    [2] 切换账号")
        print("    [3] 删除账号")
        print("    [0] 返回上级菜单")
        try:
            choice = input("    选择: ").strip().lower()
        except EOFError:
            choice = "0"
        if choice == "1":
            name = input("    账号名称: ").strip()
            if not name:
                print("    账号名不能为空。")
                continue
            if name in accounts.get("accounts", {}):
                print("    账号已存在。")
                continue
            cookie = input_cookie_from_user()
            if not cookie:
                print("    未提供 Cookie, 取消添加。")
                continue
            if not validate_cookie(cookie):
                print("    [!] Cookie 无效, 取消添加。")
                continue
            accounts["accounts"][name] = {"cookie": cookie}
            save_accounts(accounts)
            print(f"    账号'{name}'已添加。")
        elif choice == "2":
            names = list(accounts.get("accounts", {}).keys())
            if not names:
                print("    还没有账号。")
                continue
            print("    账号列表:")
            for i, n in enumerate(names, 1):
                cur = " (当前)" if n == accounts.get("current") else ""
                print(f"      {i}. {n}{cur}")
            try:
                ci = input("    选择要切换的账号(数字): ").strip()
            except EOFError:
                ci = ""
            if ci.isdigit() and 1 <= int(ci) <= len(names):
                accounts["current"] = names[int(ci) - 1]
                save_accounts(accounts)
                print(f"    已切换到'{accounts['current']}'。")
        elif choice == "3":
            names = list(accounts.get("accounts", {}).keys())
            if not names:
                print("    还没有账号。")
                continue
            print("    账号列表:")
            for i, n in enumerate(names, 1):
                cur = " (当前)" if n == accounts.get("current") else ""
                print(f"      {i}. {n}{cur}")
            try:
                ci = input("    选择要删除的账号(数字): ").strip()
            except EOFError:
                ci = ""
            if ci.isdigit() and 1 <= int(ci) <= len(names):
                doomed = names[int(ci) - 1]
                del accounts["accounts"][doomed]
                if accounts.get("current") == doomed:
                    rem = list(accounts.get("accounts", {}).keys())
                    accounts["current"] = rem[0] if rem else None
                save_accounts(accounts)
                print(f"    已删除'{doomed}'。")
        elif choice == "0":
            return
        else:
            print("    输入无效。")


# ───────────────────────── 主流程 ─────────────────────────

def main():
    ap = argparse.ArgumentParser(description="番茄书架批量下载器 v3")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=18423)
    ap.add_argument("--password", default=None)
    ap.add_argument("--format", default="txt", choices=["txt", "epub", "pdf"])
    ap.add_argument("--workers", type=int, default=2, help="并发下载实例数")
    ap.add_argument("--poll", type=float, default=2.0)
    args = ap.parse_args()

    print("=" * 60)
    print("番茄书架批量下载器 v3")
    print("=" * 60)

    accounts = load_accounts()
    cur_name, cookie = get_current_cookie(accounts)
    if not cookie:
        if os.path.isfile("cookie.txt"):
            cookie = open("cookie.txt", encoding="utf-8").read().strip()
            cur_name = cur_name or "默认账号"
            if cookie:
                accounts.setdefault("accounts", {})[cur_name] = {"cookie": cookie}
                accounts["current"] = cur_name
                save_accounts(accounts)

    if not cookie:
        print("\n[!] 没有可用的账号 Cookie。")
        cookie = input_cookie_from_user()
        if not cookie:
            print("未提供 Cookie, 退出。")
            sys.exit(2)

    print("\n[1/4] 校验当前账号 Cookie...")
    shelf_data = validate_cookie(cookie)
    if shelf_data is None:
        print("[!] Cookie 无效或已过期。")
        if input("    是否打开浏览器登录页? [Y/n]: ").strip().lower() in ("", "y", "yes"):
            open_login_browser()
            cookie = input_cookie_from_user()
            if cookie:
                shelf_data = validate_cookie(cookie)
            if shelf_data is None:
                print("[!] 新 Cookie 仍无效, 退出。")
                sys.exit(3)
        else:
            sys.exit(3)

    print("\n[2/4] 当前账号:", cur_name or "(未命名)")
    try:
        switch = input("    是否切换账号? [Y/n]: ").strip().lower()
    except EOFError:
        switch = "n"
    if switch in ("y", "yes"):
        open_login_browser()
        new_cookie = input_cookie_from_user()
        if new_cookie:
            if validate_cookie(new_cookie):
                if cur_name and cur_name in accounts.get("accounts", {}):
                    accounts["accounts"][cur_name]["cookie"] = new_cookie
                else:
                    cur_name = cur_name or f"账号{len(accounts.get('accounts', {})) + 1}"
                    accounts.setdefault("accounts", {})[cur_name] = {"cookie": new_cookie}
                accounts["current"] = cur_name
                save_accounts(accounts)
                cookie = new_cookie
                shelf_data = validate_cookie(cookie)
                print("    已更新当前账号 Cookie。")
            else:
                print("    [!] 新 Cookie 无效, 沿用当前账号。")
        else:
            print("    未提供新 Cookie, 沿用当前账号。")

    print("\n[3/4] 获取书籍...")
    if shelf_data is None:
        shelf_data = validate_cookie(cookie)
    if shelf_data is None:
        print("[!] 无法获取书籍, Cookie 可能失效。")
        sys.exit(4)
    shelf_counter, shelf_bookmap = parse_shelf_groups(shelf_data)
    print(f"    书架共 {sum(shelf_counter.values())} 本, {len(shelf_counter)} 个分组:")
    for i, (g, c) in enumerate(shelf_counter.items(), 1):
        print(f"      [{i}] {g}  ({c} 本)")

    # 启动下载器实例池
    print(f"\n[4/4] 启动 {args.workers} 个下载器实例...")
    pool = DownloaderPool(os.getcwd(), args.workers, args.password)
    if not pool.start():
        print("[!] 下载器实例启动失败, 退出。")
        sys.exit(5)

    # 主菜单
    while True:
        print("\n" + "=" * 60)
        print("  1  下载指定分组书籍(多实例并发+进度)")
        print("  2  书籍移动分组")
        print("  Z  账号设置")
        print("  0  退出")
        print("=" * 60)
        try:
            choice = input("请选择: ").strip().lower()
        except EOFError:
            choice = "0"
        if choice == "1":
            sd = validate_cookie(cookie)
            if not sd:
                print("[!] 书架刷新失败, Cookie 可能失效。")
                continue
            shelf_data = sd
            shelf_counter, shelf_bookmap = parse_shelf_groups(shelf_data)
            func_download(pool, args.format, shelf_counter, shelf_bookmap, args.workers)
        elif choice == "2":
            sd = validate_cookie(cookie)
            if not sd:
                print("[!] 书架刷新失败, Cookie 可能失效。")
                continue
            shelf_data = sd
            func_move(cookie, shelf_data)
        elif choice == "z":
            func_account_settings(accounts)
        elif choice == "0":
            print("再见。")
            pool.shutdown()
            return
        else:
            print("输入无效, 请重新选择。")


if __name__ == "__main__":
    main()
