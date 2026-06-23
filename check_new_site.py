import requests
r = requests.get("https://aihot.instantech.cn/", headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
print(f"状态: {r.status_code}, 长度: {len(r.text)}")
# 看有没有 timeline-item 结构
has_timeline = 'timeline-item' in r.text
has_m_rows = 'm-rows' in r.text
print(f"有 timeline-item: {has_timeline}")
print(f"有 m-rows: {has_m_rows}")
if has_timeline:
    import re
    items = re.findall(r'<div[^>]*class="[^"]*timeline-item[^"]*"[^>]*>', r.text)
    print(f"找到 {len(items)} 个 timeline-item")
    # 看时间戳
    times = re.findall(r'class="timeline-time"[^>]*>([^<]+)', r.text)
    print(f"时间戳示例: {times[:5]}")
