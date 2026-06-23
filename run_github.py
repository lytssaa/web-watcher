#!/usr/bin/env python3
"""
GitHub Actions 入口脚本
从环境变量读取配置，创建 config.json，然后运行 watcher 主逻辑
"""
import os
import json
import sys
from datetime import datetime

CONFIG_FILE = "config.json"
SNAPSHOT_FILE = "snapshot.json"


def main():
    # 从环境变量读取配置
    required_vars = {
        "SMTP_SERVER": "smtp_server",
        "SMTP_PORT": "smtp_port",
        "SMTP_USER": "smtp_user",
        "SMTP_PASSWORD": "smtp_password",
        "RECIPIENT": "recipients",
        "TARGET_URL": "target_url",
    }

    errors = []
    config = {
        "check_interval_minutes": 240,
        "max_pages": 3,
    }

    for env_key, cfg_key in required_vars.items():
        val = os.environ.get(env_key)
        if not val:
            errors.append(env_key)
        if cfg_key == "smtp_port":
            config[cfg_key] = int(val)
        elif cfg_key == "recipients":
            config[cfg_key] = [v.strip() for v in val.split(",") if v.strip()]
        elif cfg_key == "target_url":
            config[cfg_key] = val
        else:
            config[cfg_key] = val

    if errors:
        print(f"[错误] 缺少环境变量: {', '.join(errors)}")
        print("请在 GitHub 仓库 Settings → Secrets and variables → Actions 中设置以下 Secrets:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)

    # 写入 config.json
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 配置已从环境变量加载")

    # 导入并运行 watcher 的主逻辑
    import watcher as w

    # 直接调用 check_once
    result = w.check_once(config)

    if result:
        print("检测到变化，邮件已发送")
    else:
        print("未检测到变化或首次运行")

    # 打印 cache 信息
    if os.path.exists(SNAPSHOT_FILE):
        snap = w.load_json(SNAPSHOT_FILE)
        if snap and "items" in snap:
            print(f"快照已保存: {len(snap['items'])} 条条目")
    else:
        print("快照文件不存在（首次运行）")


if __name__ == "__main__":
    main()
