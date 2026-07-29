#!/usr/bin/env python3
"""一键更新 MP Session（替代手动复制 Cookie + 粘贴 Python 命令）

用法：
    python update_session.py
或直接双击 update_session.bat

流程：
    1. 自动打开微信公众平台登录页
    2. 你用微信扫码登录
    3. 按 F12 → Network → 随便点一个请求 → 复制 Request Headers 里的 Cookie
    4. 回到本窗口，右键粘贴 Cookie，回车
    5. 复制浏览器地址栏 token= 后面的数字，回车
    6. 脚本自动写入 session.json 并验证

依赖：webbrowser（内置）、requests（已装）
"""
import json
import os
import sys
import webbrowser
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SESSION_PATH = BASE / "wechat-mp-reader" / "scripts" / "cache" / "session.json"
MP_LOGIN_URL = "https://mp.weixin.qq.com/"


def main():
    print("=" * 50)
    print("MP Session 一键更新")
    print("=" * 50)

    # Step 1: 打开登录页
    print("\n[1/4] 正在打开微信公众平台登录页...")
    try:
        webbrowser.open(MP_LOGIN_URL, new=1)
        print("      ✓ 浏览器已打开，请用微信扫码登录")
    except Exception as e:
        print(f"      ! 自动打开失败，请手动访问：{MP_LOGIN_URL}")
        print(f"      错误：{e}")

    # Step 2: 复制 Cookie
    print("\n[2/4] 登录成功后：")
    print("      F12 → Network → 随便点一个请求")
    print("      → Request Headers → 找到 'Cookie:' 那一行")
    print("      → 选中整段 Cookie 值，Ctrl+C 复制")
    cookie = input("\n      粘贴 Cookie（右键粘贴后回车）:\n      > ").strip()
    if not cookie:
        print("      ✗ Cookie 为空，退出")
        sys.exit(1)

    # Step 3: 复制 token
    print("\n[3/4] 看浏览器地址栏 URL，找到 token= 后面的数字")
    print("      例如：...token=123456789")
    token = input("      粘贴 token 数字:\n      > ").strip()
    # 容错：如果粘贴了完整 URL，提取 token
    if "token=" in token:
        try:
            token = token.split("token=")[1].split("&")[0].split("/")[0]
        except Exception:
            pass
    if not token:
        print("      ✗ token 为空，退出")
        sys.exit(1)

    # Step 4: 写入并验证
    print("\n[4/4] 正在写入 session.json 并验证...")
    SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = {"token": token, "cookie": cookie}
    SESSION_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"      ✓ 已写入：{SESSION_PATH}")

    # 验证
    try:
        sys.path.insert(0, str(BASE / "wechat-mp-reader" / "scripts"))
        from wechat_mp_reader import check_session, resolve_session
        status = check_session(resolve_session(str(SESSION_PATH)))
        if status.get("valid"):
            print(f"      ✓ 验证成功！session 有效期约 7-14 天")
            print(f"      ✓ 下次过期时微信会收到提醒，重复本流程即可")
        else:
            print(f"      ✗ 验证失败：{status.get('reason', '未知原因')}")
            print(f"      请检查 Cookie 是否复制完整（从 ua_id= 或 slave_sid= 开始）")
            sys.exit(1)
    except Exception as e:
        print(f"      ! 验证跳过（{e}），但 session 已写入")
        print(f"      你可以手动运行：python wechat-mp-reader/scripts/wechat_mp_reader.py session check")

    print("\n" + "=" * 50)
    print("更新完成！可以重新运行周报了：")
    print("  .\\run_weekly.bat")
    print("=" * 50)


if __name__ == "__main__":
    main()
