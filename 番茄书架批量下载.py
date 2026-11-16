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


# ───────────────────────── 设置管理 ─────────────────────────

SETTINGS_FILE = "settings.json"
DEFAULT_SETTINGS = {
    "workers": 4,          # 并发下载实例数
    "format": "txt",       # 输出格式 txt/epub/pdf
}


def load_settings():
    """读取本地设置; 不存在或损坏则返回默认值。"""
    cfg = dict(DEFAULT_SETTINGS)
    if os.path.isfile(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                if isinstance(data.get("workers"), int) and 1 <= data["workers"] <= 16:
                    cfg["workers"] = data["workers"]
                if data.get("format") in ("txt", "epub", "pdf"):
                    cfg["format"] = data["format"]
        except Exception:
            pass
    return cfg


def save_settings(cfg):
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def func_settings(cfg):
    """设置菜单: 并发数 / 输出格式。返回可能被修改的 cfg。"""
    print("\n--- 设置 ---")
    while True:
        print(f"    [1] 并发下载数  当前: {cfg['workers']}  (1-16)")
        print(f"    [2] 输出格式    当前: {cfg['format']}")
        print("    [0] 返回上级菜单")
        try:
            choice = input("    选择: ").strip().lower()
        except EOFError:
            choice = "0"
        if choice == "1":
            try:
                v = input(f"    输入并发数(1-16, 当前 {cfg['workers']}): ").strip()
            except EOFError:
                v = ""
            if not v:
                print("    未修改。")
                continue
            if not v.isdigit() or not (1 <= int(v) <= 16):
                print("    输入无效, 应为 1-16 的整数。")
                continue
            newv = int(v)
            if newv == cfg["workers"]:
                print("    数值未变化。")
                continue
            cfg["workers"] = newv
            save_settings(cfg)
            print(f"    并发数已设为 {newv}。")
            print("    注意: 新并发数将在下次启动程序时生效(实例池需重建)。")
        elif choice == "2":
            try:
                v = input("    输入格式(txt/epub/pdf): ").strip().lower()
            except EOFError:
                v = ""
            if v not in ("txt", "epub", "pdf"):
                print("    输入无效, 可选 txt/epub/pdf。")
                continue
            cfg["format"] = v
            save_settings(cfg)
            print(f"    输出格式已设为 {v}。")
        elif choice == "0":
            return cfg
        else:
            print("    输入无效。")


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

    def job_detail(self, port, job_id):
        """返回完整 job 信息 dict(id/state/title/author/progress), 不存在返回 None。"""
        j = self._req(port, f"/api/jobs?id={job_id}")
        items = j.get("items") or []
        if not items:
            return None
        return items[0]

    def shutdown(self):
        for inst in self.instances:
            try:
                inst["proc"].terminate()
            except Exception:
                pass


# ───────────────────────── 下载核对 / 报告 ─────────────────────────

def list_instance_book_files(pool):
    """收集所有实例目录下的书籍文件: [(文件名, 完整路径, 实例目录)]。"""
    exts = (".txt", ".epub", ".pdf")
    found = []
    for inst in pool.instances:
        d = inst["dir"]
        if not os.path.isdir(d):
            continue
        for f in os.listdir(d):
            if f.lower().endswith(exts):
                found.append((f, os.path.join(d, f), d))
    return found


def count_expected_files(pool):
    """统计已生成的书籍文件数(用于数量核对)。"""
    return len(list_instance_book_files(pool))


def move_extra_files(extra_files, group_name="多余书籍"):
    """把多出来的书籍文件移动到单独分组目录。返回移动数量。"""
    if not extra_files:
        return 0
    dest_dir = create_local_group(group_name)
    moved = 0
    for _fname, full, _d in extra_files:
        try:
            shutil.move(full, os.path.join(dest_dir, os.path.basename(full)))
            moved += 1
        except Exception as e:
            print(f"    [!] 移动多余文件失败 {os.path.basename(full)}: {e}")
    return moved


def write_download_report(report_data, gname):
    """把下载核对结果写入本地 txt 报告。返回报告路径。"""
    now = datetime.now()
    ts_filename = now.strftime("%y%m%d%H%M%S")
    report_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "下载报告")
    os.makedirs(report_dir, exist_ok=True)
    path = os.path.join(report_dir, f"下载核对报告_{ts_filename}.txt")

    lines = []
    lines.append("=" * 60)
    lines.append("番茄小说批量下载 - 下载核对报告")
    lines.append("=" * 60)
    lines.append(f"生成时间    : {now.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"书架分组    : {report_data.get('group', gname)}")
    lines.append(f"下载格式    : {report_data.get('format', '')}")
    lines.append(f"并发实例数  : {report_data.get('workers', '')}")
    lines.append("")
    lines.append("-" * 60)
    lines.append("【数量核对】")
    lines.append("-" * 60)
    lines.append(f"预期下载数量: {report_data.get('expected', 0)} 本")
    lines.append(f"实际落盘数量: {report_data.get('actual', 0)} 本")
    lines.append(f"下载成功任务: {report_data.get('ok', 0)} 本")
    lines.append(f"下载失败任务: {report_data.get('fail', 0)} 本")
    missing_n = len(report_data.get("missing_books") or [])
    extra_n = len(report_data.get("extra_files") or [])
    if missing_n == 0 and extra_n == 0:
        lines.append("核对结论    : 【一致】书籍数量完全匹配")
    else:
        parts = []
        if missing_n:
            parts.append(f"缺少 {missing_n} 本")
        if extra_n:
            parts.append(f"多余 {extra_n} 个文件")
        lines.append("核对结论    : 【不一致】" + ", ".join(parts))
    lines.append("")

    missing = report_data.get("missing_books", [])
    if missing:
        lines.append("-" * 60)
        lines.append("【缺少的书籍】")
        lines.append("-" * 60)
        for bid in missing:
            lines.append(f"  - {bid}")
        lines.append("")

    extras = report_data.get("extra_files", [])
    if extras:
        lines.append("-" * 60)
        lines.append("【多余的文件(已移入单独分组)】")
        lines.append("-" * 60)
        for name in extras:
            lines.append(f"  - {name}")
        lines.append("")

    retried = report_data.get("retried", [])
    if retried:
        lines.append("-" * 60)
        lines.append("【已尝试重新下载】")
        lines.append("-" * 60)
        for item in retried:
            lines.append(f"  - {item}")
        lines.append("")

    lines.append("-" * 60)
    lines.append("【下载成功的书籍文件】")
    lines.append("-" * 60)
    files = report_data.get("files", [])
    if files:
        for f in files:
            lines.append(f"  - {f}")
    else:
        lines.append("  (无)")
    lines.append("")
    lines.append("=" * 60)
    lines.append("报告结束")
    lines.append("=" * 60)

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


def verify_download_result(pool, book_ids, gname, fmt, workers, ok, fail, results):
    """下载后精确核对。
    results: {book_id: {state, title, author}}
    核对依据: 每本书的 title 与磁盘文件名匹配。
    差异处理: 多余文件移入单独分组; 缺少的书籍尝试重新下载。
    最后输出 txt 报告。
    """
    expected = len(book_ids)

    def scan():
        """按标题匹配磁盘文件, 返回 (已落盘book_id集合, 未匹配文件列表)。"""
        files = list_instance_book_files(pool)
        titles = {bid: ((results.get(bid) or {}).get("title") or "") for bid in book_ids}
        landed = set()
        used = set()
        for bid, title in titles.items():
            if not title:
                continue
            key = title.strip()
            for i, (fname, _full, _d) in enumerate(files):
                if i in used:
                    continue
                stem = os.path.splitext(fname)[0]
                if stem == key or key in stem or stem in key:
                    landed.add(bid)
                    used.add(i)
                    break
        leftovers = [files[i] for i in range(len(files)) if i not in used]
        return landed, leftovers

    landed, leftovers = scan()
    report = {
        "group": gname, "format": fmt, "workers": workers,
        "expected": expected,
        "actual": len(landed),
        "ok": ok, "fail": fail,
        "missing_books": [], "extra_files": [], "retried": [],
        "files": sorted(os.path.splitext(f[0])[0] for f in list_instance_book_files(pool)),
    }

    print("\n" + "=" * 60)
    print("    下载后核对 (按书名精确比对)")
    print("=" * 60)
    print(f"    预期数量: {expected} 本")
    print(f"    已落盘  : {len(landed)} 本")

    # 缺少 -> 重新下载
    missing = [bid for bid in book_ids if bid not in landed]
    if missing:
        print(f"\n    缺少 {len(missing)} 本, 尝试重新下载:")
        for bid in missing:
            t = (results.get(bid) or {}).get("title") or ""
            print(f"      - {bid} {t}")
        report["missing_books"] = [
            (f"{bid} {(results.get(bid) or {}).get('title') or ''}").strip() for bid in missing]

        retry_ok, retry_fail, retry_results = download_group_books(pool, missing, fmt)
        results.update(retry_results)
        report["retried"] = [
            f"{bid} {(retry_results.get(bid) or {}).get('title') or ''} -> "
            f"{(retry_results.get(bid) or {}).get('state') or '未返回'}"
            for bid in missing]
        print(f"    重下结果: 成功 {retry_ok}, 失败 {retry_fail}")
        landed, leftovers = scan()
        report["actual"] = len(landed)
        report["files"] = sorted(
            os.path.splitext(f[0])[0] for f in list_instance_book_files(pool))
        still = [bid for bid in missing if bid not in landed]
        if still:
            print(f"    重下后仍缺少 {len(still)} 本")
        else:
            print("    重下后已补齐")

    # 多余 -> 移入单独分组
    if leftovers:
        print(f"\n    发现 {len(leftovers)} 个多余文件, 移入单独分组:")
        for fname, _full, _d in leftovers:
            print(f"      - {fname}")
        moved = move_extra_files(leftovers, "多余书籍")
        report["extra_files"] = [os.path.splitext(f[0])[0] for f in leftovers]
        print(f"    已移动 {moved} 个到本地分组'多余书籍'")

    # 结论
    print("\n    核对结论:")
    if report["actual"] == expected and not leftovers:
        print(f"      【一致】{expected} 本全部下载完成")
    else:
        print(f"      预期 {expected} 本, 实际 {report['actual']} 本, "
              f"多余 {len(report['extra_files'])} 个")

    path = write_download_report(report, gname)
    print(f"    核对报告: {path}")
    return path


# ───────────────────────── 下载流程(多实例并发) ─────────────────────────

def download_group_books(pool, book_ids, fmt):
    """多实例并发下载一组书籍。返回 (ok, fail, results)。
    results: {book_id: {"state": "done"/"failed"/"timeout", "title": str, "author": str}}
    """
    pool.set_format(fmt)
    # 提交所有书到实例池
    queue = list(enumerate(book_ids))  # (原序号, book_id)
    pending = []  # [原序号, book_id, inst_idx, job_id]
    total = len(queue)
    results = {}  # book_id -> {state, title, author}
    # 初始提交
    for idx, bid in queue:
        inst_idx, job_id = pool.submit(bid)
        if inst_idx is not None:
            pending.append([idx, bid, inst_idx, job_id])
    # 轮询进度
    ok, fail = 0, 0
    while pending:
        time.sleep(2)
        still = []
        progress_lines = []
        for p in pending:
            idx, bid, inst_idx, job_id = p
            inst = pool.instances[inst_idx]
            detail = pool.job_detail(inst["port"], job_id)
            if detail is None:
                still.append(p)
                continue
            state = detail.get("state")
            if state == "done":
                ok += 1
                results[bid] = {
                    "state": "done",
                    "title": detail.get("title") or "",
                    "author": detail.get("author") or "",
                }
                print(f"    [完成 {ok+fail}/{total}] {bid} {detail.get('title') or ''}")
            elif state == "failed":
                fail += 1
                results[bid] = {
                    "state": "failed",
                    "title": detail.get("title") or "",
                    "author": detail.get("author") or "",
                }
                print(f"    [失败 {ok+fail}/{total}] {bid} {detail.get('title') or ''}")
            else:
                saved = (detail.get("progress") or {}).get("saved_chapters", 0)
                total_ch = (detail.get("progress") or {}).get("chapter_total", 0)
                pct = (saved / total_ch * 100) if total_ch else 0
                bar = "#" * int(pct // 5)
                progress_lines.append(
                    f"    实例{inst_idx+1} {pct:5.1f}% [{bar:<20}] {saved}/{total_ch}")
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
                if bid in results:
                    continue
                inst_idx, job_id = pool.submit(bid)
                if inst_idx is not None:
                    pending.append([idx, bid, inst_idx, job_id])
        pending = still
    return ok, fail, results


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

    # 下载前确认总数量
    print("\n" + "=" * 60)
    print("    下载前确认")
    print("=" * 60)
    print(f"    目标分组  : {gname}")
    print(f"    书籍总数  : {len(books)} 本")
    print(f"    并发实例  : {workers}")
    print(f"    输出格式  : {fmt}")
    print("=" * 60)
    try:
        confirm = input("    确认开始下载? [Y/n]: ").strip().lower()
    except EOFError:
        confirm = "y"
    if confirm not in ("y", "yes", ""):
        print("    已取消下载。")
        return

    print(f"\n    开始下载分组'{gname}'共 {len(books)} 本...")
    ok, fail, results = download_group_books(pool, books, fmt)
    print(f"\n    下载完成: 成功 {ok}, 失败 {fail}")
    # 下载后核对(按书名精确比对 + 差异处理 + 生成txt报告)
    report_path = verify_download_result(
        pool, books, gname, fmt, workers, ok, fail, results)
    # 自动整理到桌面
    organize_to_desktop(pool)
    if report_path:
        print(f"    核对报告: {report_path}")
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
    ap.add_argument("--format", default=None, choices=["txt", "epub", "pdf"],
                    help="输出格式(默认读设置, 首次为 txt)")
    ap.add_argument("--workers", type=int, default=None,
                    help="并发下载实例数(默认读设置, 首次为 4)")
    ap.add_argument("--poll", type=float, default=2.0)
    args = ap.parse_args()

    # 载入本地设置(CLI 参数优先)
    settings = load_settings()
    if args.workers is not None:
        settings["workers"] = max(1, min(16, args.workers))
    if args.format:
        settings["format"] = args.format

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
    print(f"\n[4/4] 启动 {settings['workers']} 个下载器实例...")
    pool = DownloaderPool(os.getcwd(), settings["workers"], args.password)
    if not pool.start():
        print("[!] 下载器实例启动失败, 退出。")
        sys.exit(5)

    # 主菜单
    while True:
        print("\n" + "=" * 60)
        print("  1  下载指定分组书籍(多实例并发+进度+核对)")
        print("  2  书籍移动分组")
        print("  Z  账号设置")
        print("  S  设置(并发数 / 输出格式)")
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
            func_download(pool, settings["format"], shelf_counter,
                          shelf_bookmap, settings["workers"])
        elif choice == "2":
            sd = validate_cookie(cookie)
            if not sd:
                print("[!] 书架刷新失败, Cookie 可能失效。")
                continue
            shelf_data = sd
            func_move(cookie, shelf_data)
        elif choice == "z":
            func_account_settings(accounts)
        elif choice == "s":
            before = settings["workers"]
            settings = func_settings(settings)
            if settings["workers"] != before:
                print("\n    并发数已变更, 正在重建下载器实例池...")
                pool.shutdown()
                time.sleep(1)
                pool = DownloaderPool(os.getcwd(), settings["workers"], args.password)
                if pool.start():
                    print(f"    已按新并发数重建 {settings['workers']} 个实例")
                else:
                    print("    [!] 重建实例池失败")
        elif choice == "0":
            print("再见。")
            pool.shutdown()
            return
        else:
            print("输入无效, 请重新选择。")


if __name__ == "__main__":
    main()
