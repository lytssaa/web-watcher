#!/usr/bin/env python3
"""
Web Watcher — 网页内容变化监控器
抓取目标网页 → 解析结构化新闻条目 → 发现新增/消失条目时发送邮件
"""

import json
import hashlib
import time
import smtplib
import email.mime.text
import os
import sys
import re
import html as html_mod
from datetime import datetime

import requests

CONFIG_FILE = "config.json"
SNAPSHOT_FILE = "snapshot.json"


# ── 页面抓取 ──────────────────────────────────────────

def fetch_page(url: str) -> str:
    """抓取目标网页，返回完整 HTML"""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        )
    }
    resp = requests.get(url, headers=headers, timeout=30)
    resp.encoding = resp.apparent_encoding or "utf-8"
    return resp.text


# ── 页面解析 ──────────────────────────────────────────

def parse_news_items(html_text: str) -> list:
    """
    从 HTML 中提取所有新闻条目。
    同时解析页面中的日期（如 6月23日）。
    """
    items = []

    # 提取页面中的日期（在 m-daybar-sub 或类似元素中）
    page_date = None
    date_m = re.search(r'(\d{1,2})月(\d{1,2})日', html_text)
    if date_m:
        month, day = int(date_m.group(1)), int(date_m.group(2))
        # 假设当前年份
        year = datetime.now().year
        page_date = f"{year}-{month:02d}-{day:02d}"
    else:
        # 没有找到日期，用今天
        page_date = datetime.now().strftime("%Y-%m-%d")

    # 找到每个 timeline-item
    item_blocks = re.findall(
        r'<div[^>]*class="[^"]*timeline-item[^"]*"[^>]*>.*?</article>\s*</div>',
        html_text, re.DOTALL
    )

    for block in item_blocks:
        item = _parse_one_item(block)
        if item and item.get("raw_text"):
            # 补充完整时间戳
            if item.get("time") and page_date:
                item["full_time"] = f"{page_date}T{item['time']}:00"
            items.append(item)

    # 如果 timeline 方式没找到，回退到 m-row 方式
    if not items:
        row_blocks = re.findall(
            r'<a[^>]*class="[^"]*m-row[^"]*"[^>]*>.*?</a>',
            html_text, re.DOTALL
        )
        for block in row_blocks:
            item = _parse_one_row(block)
            if item and item.get("raw_text"):
                if item.get("time") and page_date:
                    item["full_time"] = f"{page_date}T{item['time']}:00"
                items.append(item)

    return items


def _parse_one_item(block: str) -> dict:
    """解析一个 timeline-item"""
    item = {}

    # ID
    id_m = re.search(r'data-item-id="([^"]+)"', block)
    if id_m:
        item["id"] = id_m.group(1)

    # 时间
    time_m = re.search(r'class="timeline-time"[^>]*>([^<]+)', block)
    if time_m:
        item["time"] = time_m.group(1).strip()

    # 来源
    source_m = re.search(r'class="timeline-source"[^>]*>([^<]+)', block)
    if source_m:
        item["source"] = source_m.group(1).strip()

    # 评分/热度
    score_m = re.search(r'class="timeline-score[^"]*"[^>]*>([^<]+)', block)
    if score_m:
        item["score"] = score_m.group(1).strip()

    # 标题 (timeline-title 优先，回退 m-row-title)
    title_m = re.search(r'class="timeline-title"[^>]*href="([^"]*)"[^>]*>([^<]+)', block)
    if title_m:
        item["title"] = title_m.group(2).strip()
        item["url"] = title_m.group(1)
    else:
        title_m = re.search(r'class="m-row-title"[^>]*>([^<]+)', block)
        if title_m:
            item["title"] = title_m.group(1).strip()
        # 从 m-row 或外层 a 标签取链接
        url_m = re.search(r'<a[^>]*href="(/items/[^"]+)"', block)
        if url_m:
            item["url"] = url_m.group(1)

    # AI 摘要 (timeline-summary)
    summary_m = re.search(r'class="timeline-summary"[^>]*>(.*?)</p>', block, re.DOTALL)
    if summary_m:
        summary_text = re.sub(r'<[^>]+>', '', summary_m.group(1)).strip()
        if summary_text:
            item["summary"] = summary_text

    # 简介 (m-row-desc)
    desc_m = re.search(r'class="m-row-desc"[^>]*>([^<]+)', block)
    if desc_m:
        item["desc"] = desc_m.group(1).strip()

    # uc-body 内容 (推文类型)
    uc_m = re.search(r'class="uc-body"[^>]*>(.*?)</a>', block, re.DOTALL)
    if uc_m:
        uc_text = re.sub(r'<[^>]+>', '', uc_m.group(1)).strip()
        if uc_text:
            if "title" not in item:
                item["title"] = uc_text[:100]
            item["body"] = uc_text

    # 推荐理由
    reason_m = re.search(r'class="timeline-reason"[^>]*>(.*?)</div>', block, re.DOTALL)
    if reason_m:
        reason_text = re.sub(r'<[^>]+>', '', reason_m.group(1)).strip()
        if reason_text:
            item["reason"] = reason_text

    # 标签
    tags = re.findall(r'class="tag[^"]*"[^>]*>([^<]+)', block)
    if tags:
        item["tags"] = [t.strip() for t in tags]

    # 构建原始文本用于哈希
    parts = []
    for key in ["time", "source", "score", "title", "desc", "reason"]:
        if key in item:
            parts.append(str(item[key]))
    if "tags" in item:
        parts.append(" ".join(item["tags"]))
    if "body" in item:
        parts.append(item["body"])
    raw = " | ".join(parts)
    if raw:
        item["raw_text"] = raw
        item["hash"] = hashlib.sha256(raw.encode("utf-8")).hexdigest()

    return item


def _parse_one_row(block: str) -> dict:
    """解析一个 m-row（移动端列表布局）"""
    item = {}

    id_m = re.search(r'data-item-id="([^"]+)"', block)
    if id_m:
        item["id"] = id_m.group(1)

    time_m = re.search(r'class="m-row-time"[^>]*>([^<]+)', block)
    if time_m:
        item["time"] = time_m.group(1).strip()

    title_m = re.search(r'class="m-row-title"[^>]*>([^<]+)', block)
    if title_m:
        item["title"] = title_m.group(1).strip()

    desc_m = re.search(r'class="m-row-desc"[^>]*>([^<]+)', block)
    if desc_m:
        item["desc"] = desc_m.group(1).strip()

    hots_m = re.search(r'class="m-row-hots"[^>]*>([^<]+)', block)
    if hots_m:
        item["score"] = hots_m.group(1).strip()

    raw = " | ".join(str(item.get(k, "")) for k in ["time", "title", "desc"])
    if raw.strip(" |"):
        item["raw_text"] = raw
        item["hash"] = hashlib.sha256(raw.encode("utf-8")).hexdigest()

    return item


# ── 快照管理 ──────────────────────────────────────────

def load_json(path: str):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def save_json(path: str, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def make_snapshot(items: list) -> dict:
    """从条目列表构建快照"""
    by_id = {}
    for item in items:
        iid = item.get("id")
        if iid:
            by_id[iid] = item
    return {
        "timestamp": datetime.now().isoformat(),
        "items": items,
        "by_id": {iid: it["hash"] for iid, it in by_id.items()},
    }


# ── 对比逻辑 ──────────────────────────────────────────

def compare_snapshots(old: dict, new: dict) -> tuple:
    """
    比较新旧快照，返回 (added_items, removed_items)
    """
    old_ids = set(old.get("by_id", {}).keys())
    new_ids = set(new.get("by_id", {}).keys())

    added_ids = new_ids - old_ids
    removed_ids = old_ids - new_ids

    # 对于相同 ID 的条目，检查内容是否变化
    changed_ids = set()
    common_ids = old_ids & new_ids
    for iid in common_ids:
        if old.get("by_id", {}).get(iid) != new.get("by_id", {}).get(iid):
            # 内容变了，当作先删除再新增
            removed_ids.add(iid)
            added_ids.add(iid)

    # 构建条目映射
    old_map = {it.get("id"): it for it in old.get("items", []) if it.get("id")}
    new_map = {it.get("id"): it for it in new.get("items", []) if it.get("id")}

    added = [new_map[iid] for iid in added_ids if iid in new_map]
    removed = [old_map[iid] for iid in removed_ids if iid in old_map]

    # 如果没有 ID 匹配（所有条目都可能有 ID），按内容哈希对比
    if not added_ids and not removed_ids and not changed_ids:
        old_raws = {it["hash"]: it for it in old.get("items", []) if it.get("hash")}
        new_raws = {it["hash"]: it for it in new.get("items", []) if it.get("hash")}
        added = [new_raws[h] for h in new_raws if h not in old_raws]
        removed = [old_raws[h] for h in old_raws if h not in new_raws]

    return added, removed


# ── 邮件生成 ──────────────────────────────────────────

def format_item_html(item: dict) -> str:
    """格式化一条新闻为漂亮的 HTML 块"""
    parts = []

    # 时间 + 来源 + 评分
    header_parts = []
    if item.get("time"):
        header_parts.append(f'<span style="color:#888; font-weight:bold;">{html_mod.escape(item["time"])}</span>')
    if item.get("source"):
        header_parts.append(f'<span style="color:#555;">{html_mod.escape(item["source"])}</span>')
    if header_parts:
        parts.append(f'<div style="font-size:13px; margin-bottom:4px;">{" ".join(header_parts)}</div>')

    # 标题
    if item.get("title"):
        parts.append(f'<div style="font-size:16px; font-weight:bold; color:#222; margin-bottom:6px; line-height:1.4;">{html_mod.escape(item["title"])}</div>')

    # AI 摘要
    if item.get("summary"):
        summary = item["summary"]
        parts.append(
            f'<div style="font-size:14px; color:#444; line-height:1.6; margin-bottom:6px; '
            f'background:#f0f7ff; border-left:3px solid #1976d2; padding:6px 10px; border-radius:2px;">'
            f'{html_mod.escape(summary)}</div>'
        )

    # 正文（desc / body 作为补充）
    body_text = item.get("desc") or item.get("body") or ""
    if body_text:
        parts.append(f'<div style="font-size:14px; color:#444; line-height:1.6; margin-bottom:4px;">{html_mod.escape(body_text)}</div>')

    # 推荐理由
    if item.get("reason"):
        reason = item["reason"]
        parts.append(
            f'<div style="font-size:13px; color:#666; margin-bottom:6px; '
            f'background:#fff8e1; border-left:3px solid #ff8f00; padding:4px 10px; border-radius:2px;">'
            f'💡 {html_mod.escape(reason)}</div>'
        )

    # 标签
    if item.get("tags"):
        tags_html = " ".join(
            f'<span style="display:inline-block; background:#e8e8e8; color:#555; font-size:12px; padding:1px 6px; border-radius:3px; margin-right:4px;">{html_mod.escape(t)}</span>'
            for t in item["tags"]
        )
        parts.append(f'<div style="margin-top:4px; margin-bottom:4px;">{tags_html}</div>')

    # 阅读原文
    if item.get("url"):
        full_url = f"https://aihot.virxact.com{item['url']}" if item["url"].startswith("/") else item["url"]
        parts.append(
            f'<div style="margin-top:4px;">'
            f'<a href="{html_mod.escape(full_url)}" style="color:#1976d2; font-size:13px; text-decoration:none;">'
            f'阅读原文 &rarr;</a></div>'
        )

    return '<div style="padding:12px 0; border-bottom:1px solid #eee;">' + "\n".join(parts) + "</div>"


def build_change_email(added: list, removed: list, url: str) -> str:
    """构建变化通知 HTML 邮件（仅显示新增条目）"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    content_parts = []

    if added:
        content_parts.append(
            f'<p style="font-size:16px; font-weight:bold; color:#2e7d32; margin:12px 0 6px 0;">'
            f'&#x25B2; 新增条目（{len(added)} 条）</p>'
        )
        for item in added:
            content_parts.append(format_item_html(item))

    if not added:
        content_parts.append('<p style="color:#888; font-size:14px;">检测到变化，但无法识别具体条目差异。</p>')

    html_email = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"></head>
<body style="margin:0; padding:0; background:#f5f5f5;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f5f5f5;">
<tr><td align="center" style="padding:20px 10px;">
<table width="100%" style="max-width:600px; background:#fff; border-radius:8px;">
<tr><td style="padding:20px 24px;">

<div style="font-size:20px; font-weight:bold; color:#d32f2f; margin-bottom:12px;">
  &#x1F514; 网页内容已变化
</div>

<table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:12px; font-size:14px; color:#888;">
<tr><td style="padding:3px 0; width:60px;" valign="top">地址</td>
    <td style="padding:3px 0;"><a href="{html_mod.escape(url)}" style="color:#1976d2; word-break:break-all;">{html_mod.escape(url)}</a></td></tr>
<tr><td style="padding:3px 0;">时间</td>
    <td style="padding:3px 0;">{timestamp}</td></tr>
</table>

<hr style="border:none; border-top:1px solid #e0e0e0; margin:12px 0;">

{"".join(content_parts)}

<hr style="border:none; border-top:1px solid #e0e0e0; margin:12px 0;">

<div style="text-align:center; margin-top:12px;">
  <a href="{html_mod.escape(url)}"
     style="display:inline-block; background:#1976d2; color:#fff; text-decoration:none;
            padding:12px 28px; border-radius:6px; font-size:16px; font-weight:bold;">
    点此查看完整页面 &rarr;
  </a>
</div>

<p style="color:#999; font-size:12px; margin:16px 0 0 0; text-align:center;">
  每 60 分钟检查一次 | Web Watcher 自动发送
</p>

</td></tr>
</table>
</td></tr>
</table>
</body>
</html>"""
    return html_email


# ── 邮件发送 ──────────────────────────────────────────

def send_email(config: dict, html_body: str):
    msg = email.mime.text.MIMEText(html_body, "html", "utf-8")
    msg["Subject"] = (
        f"[通知] 网页变化通知 — {config['target_url']}"
        f" — {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    )
    msg["From"] = config["smtp_user"]
    msg["To"] = ", ".join(config["recipients"])

    with smtplib.SMTP(config["smtp_server"], config["smtp_port"]) as server:
        server.starttls()
        server.login(config["smtp_user"], config["smtp_password"])
        server.sendmail(config["smtp_user"], config["recipients"], msg.as_string())


def send_simple_notification(config: dict):
    msg = email.mime.text.MIMEText(
        f"网页内容已更新，请检查：{config['target_url']}\n"
        f"检测时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "plain", "utf-8"
    )
    msg["Subject"] = f"网页变化通知 — {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    msg["From"] = config["smtp_user"]
    msg["To"] = ", ".join(config["recipients"])

    with smtplib.SMTP(config["smtp_server"], config["smtp_port"]) as server:
        server.starttls()
        server.login(config["smtp_user"], config["smtp_password"])
        server.sendmail(config["smtp_user"], config["recipients"], msg.as_string())


# ── 配置管理 ──────────────────────────────────────────

def interactive_setup() -> dict:
    print("=" * 56)
    print("  [Web Watcher] — 首次配置向导")
    print("=" * 56)
    print("请准备好 SMTP 发件信息（推荐 QQ邮箱 / Gmail 应用专用密码）\n")

    config = {
        "target_url": "https://aihot.virxact.com/all",
        "check_interval_minutes": 60,
        "smtp_server": input("SMTP 服务器 (如 smtp.qq.com): ").strip(),
        "smtp_port": int(input("SMTP 端口 (如 587): ").strip()),
        "smtp_user": input("发件邮箱地址: ").strip(),
        "smtp_password": input("SMTP 应用专用密码: ").strip(),
        "recipients": [input("收件邮箱地址: ").strip()],
    }

    save_json(CONFIG_FILE, config)
    print("\n配置已保存到 config.json")
    return config


# ── 主流程 ──────────────────────────────────────────

def check_once(config: dict) -> bool:
    """执行一次抓取→解析→按时间过滤→通知流程"""
    url = config["target_url"]
    max_pages = config.get("max_pages", 10)
    
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 正在抓取 {url} ...")

    # 加载旧快照，获取上次检查时间
    old_snapshot = load_json(SNAPSHOT_FILE)
    since_time = None
    if old_snapshot and "checked_at" in old_snapshot:
        since_time = old_snapshot["checked_at"]
        print(f"  上次检查时间: {since_time}")

    # 分离基础 URL
    base_url = url.split("?")[0]
    now_ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    all_items = []
    seen_ids = set()
    hit_old_content = False  # 遇到旧内容就停

    for page in range(1, max_pages + 1):
        if hit_old_content:
            print(f"  已遇到旧内容，停止翻页")
            break

        page_url = f"{base_url}?page={page}"
        try:
            html = fetch_page(page_url)
        except Exception as e:
            print(f"  第{page}页抓取失败: {e}")
            continue

        items = parse_news_items(html)

        new_in_page = 0
        for item in items:
            iid = item.get("id")
            # 去重
            if iid and iid in seen_ids:
                continue
            if iid:
                seen_ids.add(iid)

            # 按时间过滤
            ft = item.get("full_time")
            if since_time and ft:
                if ft <= since_time:
                    # 遇到旧内容，后面的更旧，停止
                    hit_old_content = True
                    break

            all_items.append(item)
            new_in_page += 1

        print(f"  第{page}页: {len(items)} 条（新 {new_in_page} 条）")

    print(f"  共 {len(all_items)} 条新增条目")

    # 构建新快照（包含本次检查时间）
    new_snapshot = make_snapshot(all_items)
    new_snapshot["checked_at"] = now_ts
    save_json(SNAPSHOT_FILE, new_snapshot)

    if not all_items:
        print("无新增内容")
        return False

    # 发送邮件
    print(f"正在发送邮件，共 {len(all_items)} 条...")
    diff_email = build_change_email(all_items, [], url)

    try:
        send_email(config, diff_email)
        print("邮件已发送到:", ", ".join(config["recipients"]))
        return True
    except smtplib.SMTPAuthenticationError:
        print("邮件认证失败")
        return False
    except Exception as e:
        print(f"发送失败: {e}")
        return False


def print_status(config: dict):
    snap = load_json(SNAPSHOT_FILE)
    print("\n" + "=" * 50)
    print("  [状态] Web Watcher 状态")
    print("=" * 50)
    print(f"  目标 URL : {config['target_url']}")
    print(f"  检查间隔 : {config['check_interval_minutes']} 分钟")
    print(f"  SMTP     : {config['smtp_server']}:{config['smtp_port']}")
    print(f"  发件人   : {config['smtp_user']}")
    print(f"  收件人   : {', '.join(config['recipients'])}")
    if snap and "items" in snap:
        print(f"  上次快照 : {snap.get('timestamp', 'N/A')}")
        print(f"  条目数量 : {len(snap['items'])} 条")
        if snap["items"]:
            print(f"  最新时间 : {snap['items'][0].get('time', 'N/A')}")
    else:
        print("  快照     : 暂无")
    print("=" * 50)


def main():
    config = load_json(CONFIG_FILE)

    if not config:
        print("未检测到配置文件")
        config = interactive_setup()

    args = set(sys.argv[1:])

    if "--status" in args or "-s" in args:
        print_status(config)
        return

    if "--reconfig" in args or "-r" in args:
        os.remove(CONFIG_FILE)
        config = interactive_setup()

    if "--help" in args or "-h" in args:
        print("""Web Watcher — 网页变化监控器

用法:
  python watcher.py             单次检查
  python watcher.py --loop      每 60 分钟循环检查
  python watcher.py --status    查看状态
  python watcher.py --reconfig  重新配置邮箱
  python watcher.py --test-email 发送测试邮件
  python watcher.py --help      显示帮助
""")
        return

    if "--test-email" in args:
        try:
            send_simple_notification(config)
            print("测试邮件已发送！请检查收件箱。")
        except Exception as e:
            print(f"测试邮件发送失败: {e}")
        return

    loop = "--loop" in args

    changed = check_once(config)

    if loop:
        interval = config["check_interval_minutes"] * 60
        failure_count = 0
        while True:
            print(f"\n等待 {config['check_interval_minutes']} 分钟后下次检查...")
            time.sleep(interval)
            try:
                changed = check_once(config)
                if changed:
                    failure_count = 0
            except Exception as e:
                failure_count += 1
                print(f"检查异常 ({failure_count}/3): {e}")
                if failure_count >= 3:
                    print("\n连续 3 次失败，已暂停。请检查网络或目标网站状态。")
                    break
    else:
        print("\n[提示] 使用 `python watcher.py --loop` 可进入定时循环模式")


if __name__ == "__main__":
    main()
