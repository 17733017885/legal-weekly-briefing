#!/usr/bin/env python3
"""Server酱微信推送模块

在周报生成后，通过 Server酱 将通知推送到用户微信。
依赖：requests（已随 wechat-mp-reader 安装）

用法：
    from notify_wechat import notify_report
    notify_report(report_path, candidates_count, imported_count, errors)

或 CLI：
    python notify_wechat.py --title "标题" --desp "内容"
"""
import json
import os
import sys
from pathlib import Path
from datetime import datetime

try:
    import requests
except ImportError:
    requests = None

# SendKey 从环境变量读取，或从配置文件读取
SERVERCHAN_KEY_FILE = Path(__file__).resolve().parent.parent / "assets" / "config" / ".serverchan_key"
SERVERCHAN_API = "https://sctapi.ftqq.com/{key}.send"


def get_sendkey():
    """从环境变量或配置文件读取 SendKey"""
    key = os.environ.get("SERVERCHAN_SENDKEY", "").strip()
    if key:
        return key
    if SERVERCHAN_KEY_FILE.exists():
        return SERVERCHAN_KEY_FILE.read_text(encoding="utf-8").strip()
    return ""


def send(title, desp=""):
    """发送 Server酱 推送

    Args:
        title: 消息标题（最长 32 字符，超出截断）
        desp: 消息内容（Markdown 格式，最长 32KB）

    Returns:
        dict: {"ok": bool, "msg": str}
    """
    key = get_sendkey()
    if not key:
        return {"ok": False, "msg": "未配置 SendKey"}

    if requests is None:
        return {"ok": False, "msg": "requests 库未安装"}

    # 标题截断
    title = (title or "通知")[:32]

    try:
        resp = requests.post(
            SERVERCHAN_API.format(key=key),
            data={"title": title, "desp": desp},
            timeout=15,
        )
        data = resp.json()
        # Server酱返回 {"code": 0, "message": "请求成功"} 表示成功
        if data.get("code") == 0:
            return {"ok": True, "msg": "推送成功"}
        return {"ok": False, "msg": data.get("message", f"HTTP {resp.status_code}")}
    except Exception as e:
        return {"ok": False, "msg": str(e)}


def notify_report(report_path=None, candidates_count=0, imported_count=0,
                  errors=None, html_path=None):
    """周报生成后推送通知到微信

    Args:
        report_path: 周报 MD 文件路径
        candidates_count: 候选文章数
        imported_count: IMA 导入数
        errors: 错误列表
        html_path: HTML 周报路径

    Returns:
        dict: 推送结果
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    title = f"法律周报已生成 {now[:10]}"

    lines = [
        f"## 法律周报生成报告",
        f"",
        f"**生成时间**：{now}",
        f"",
        f"**候选文章**：{candidates_count} 篇",
        f"",
        f"**IMA 导入**：{imported_count} 篇",
        f"",
    ]

    if errors:
        lines.append(f"**错误信息**：")
        lines.append("")
        for err in errors[:5]:  # 最多显示5条错误
            lines.append(f"- {err}")
        lines.append("")

    if report_path:
        lines.append(f"**周报文件**：`{Path(report_path).name}`")
        lines.append("")

    if html_path:
        lines.append(f"**HTML 周报**：`{Path(html_path).name}`")
        lines.append("")

    lines.append("---")
    lines.append("*此消息由法律周报自动生成系统推送*")

    desp = "\n".join(lines)
    return send(title, desp)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Server酱微信推送")
    parser.add_argument("--title", default="测试通知", help="消息标题")
    parser.add_argument("--desp", default="", help="消息内容（Markdown）")
    args = parser.parse_args()

    result = send(args.title, args.desp)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["ok"]:
        sys.exit(1)
