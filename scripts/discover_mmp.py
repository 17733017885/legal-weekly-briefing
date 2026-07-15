#!/usr/bin/env python3
"""MP 内容发现层（legal-weekly-briefing 的 Level 3 适配器）。

职责：
  1. 读取 sources.yaml 里的法院公众号账号（含 fakeid）
  2. 调用 wechat-mp-reader 按 fakeid 从 MP 后台拉取近期文章
  3. 启发式自动标注 features（评分引擎必需，文章本身不带）
  4. 写出 candidates.jsonl 供 run_pipeline.py 消费

这是原技能缺失的"内容发现"环节——官方文档里的 wechat-ocr-research
已下架，这里用功能等价的开源项目 wechat-mp-reader 顶上。

前置：
  - wechat-mp-reader 已装在 <skill_dir>/skills/wechat-mp-reader
  - 已通过二维码登录拿到 MP session
    （环境变量 WECHAT_MP_COOKIE + WECHAT_MP_TOKEN，或
      skills/wechat-mp-reader/scripts/wechat_mp_reader/cache/session.json）

用法：
  python3 scripts/discover_mmp.py                 # 默认输出 candidates.jsonl
  python3 scripts/discover_mmp.py --mock           # 不联网，用内置样例验证管线
  python3 scripts/discover_mmp.py --reader /path/to/wechat-mp-reader/scripts
"""
import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SRC_YAML = BASE / "assets" / "config" / "sources.yaml"

# ---- 默认账号（sources.yaml 缺失时的兜底，含官方预置 fakeid）----
DEFAULT_ACCOUNTS = [
    {"name": "山东高法", "fakeid": "MzA5MDAxMjk5Ng=="},
    {"name": "上海一中院", "fakeid": "MjM5MjkwMDkxMA=="},
    {"name": "上海二中院", "fakeid": "MzA4MzY3NjMxNw=="},
]

# ---- 兴趣赛道关键词（决定 relevance=1 还是 2；可按执业方向改）----
INTEREST_KEYWORDS = [
    "婚姻", "家事", "抚养", "继承", "离婚", "彩礼", "遗嘱",
    "公司", "股东", "股权", "决议", "章程", "法人",
    "合同", "借贷", "债务", "定金", "买卖",
    "建工", "建设工程", "施工", "包工头", "以房抵债",
    "劳动", "工伤", "调岗", "解雇", "社保",
    "交通事故", "道交", "保险", "代驾", "肇事",
    "房地产", "物业", "业主", "交房", "拆迁", "漏水",
    "侵权", "受伤", "高空抛物",
    "刑事", "罪名", "命案", "诈骗",
    "管辖", "仲裁", "主管",
    "知识产权", "商标", "专利", "著作权",
]

# 体系化方法论信号（命中则 depth=1，否则默认 2=个案叙事）
METHOD_SIGNALS = [
    "方法论", "类型化", "审查要点", "规则梳理", "体系", "要点",
    "全解", "指引", "问答", "实务指引", "办案指引", "裁判规则",
    "类案", "裁判要旨", "理解与适用", "解读",
]

# AI+法律 信号
AI_SIGNALS = [
    "AI", "大模型", "人工智能", "算法", "智能体", "agent", "GPT",
    "法律科技", "法律AI", "法律 人工智能", "生成式", "算力",
]


def load_accounts():
    """优先读 sources.yaml，缺失则用默认账号。"""
    if SRC_YAML.exists():
        try:
            import yaml
            with open(SRC_YAML, encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            mp = cfg.get("mp", {}) or {}
            accounts = mp.get("accounts") or []
            if accounts:
                return [
                    {"name": a.get("name", ""), "fakeid": a.get("fakeid", "")}
                    for a in accounts if a.get("fakeid")
                ]
        except Exception as e:
            print(f"[warn] 读 sources.yaml 失败，用默认账号: {e}", file=sys.stderr)
    return list(DEFAULT_ACCOUNTS)


def load_reader(reader_path):
    """导入 wechat-mp-reader 的 Python 接口。

    自动探测常见安装位置（避免硬编码路径指错）：
      - 显式 --reader
      - 环境变量 WECHAT_MP_READER
      - <skill_dir 的兄弟>/wechat-mp-reader/scripts
        （如 /workspace/skills/legal-weekly-briefing 的兄弟
         /workspace/skills/wechat-mp-reader/scripts）
      - <skill_dir>/skills/wechat-mp-reader/scripts
        （文档约定的 skill 布局）
    """
    candidates = []
    if reader_path:
        candidates.append(Path(reader_path))
    env = os.environ.get("WECHAT_MP_READER")
    if env:
        candidates.append(Path(env))
    # 兄弟目录：BASE.parent / wechat-mp-reader / scripts
    candidates.append(BASE.parent / "wechat-mp-reader" / "scripts")
    # 文档 skill 布局：BASE / skills / wechat-mp-reader / scripts
    candidates.append(BASE / "skills" / "wechat-mp-reader" / "scripts")

    for rp in candidates:
        if rp.exists():
            if str(rp) not in sys.path:
                sys.path.insert(0, str(rp))
            import wechat_mp_reader as wmr
            return wmr

    raise SystemExit(
        f"[fatal] 找不到 wechat-mp-reader/scripts，请先安装：\n"
        f"  git clone https://github.com/nasplycc/wechat-mp-reader.git <skill_dir>/skills/wechat-mp-reader\n"
        f"  或指定 --reader /path/to/wechat-mp-reader/scripts\n"
        f"  或设环境变量 WECHAT_MP_READER=/path/to/wechat-mp-reader/scripts"
    )


def heuristic_label(title, source):
    """启发式给一条文章打 features（评分引擎必需）。

    维度取值见 SKILL.md 评分锚定表。
    说明：这是无 LLM 时的自动兜底，质量够排序用；
    如需更准，可改为调用 LLM 逐条标注。
    """
    t = title or ""
    tl = t.lower()

    # category
    cat = "ai-legal" if any(k.lower() in tl for k in AI_SIGNALS) else "legal"

    # author_tier: 法院官方号（高院/中院）→ 2；其余 → 3
    if any(k in source for k in ("高法", "高院", "一中院", "二中院", "中院", "最高法")):
        author_tier = 2
    else:
        author_tier = 3

    # platform_tier: 法院品牌栏目 → 3
    platform_tier = 3

    # depth: 方法论信号 → 1，否则 2（个案叙事）
    depth = 1 if any(s in t for s in METHOD_SIGNALS) else 2

    # relevance: 命中兴趣赛道 → 1，否则 2
    relevance = 1 if any(k in t for k in INTEREST_KEYWORDS) else 2

    return {
        "author_tier": author_tier,
        "platform_tier": platform_tier,
        "depth": depth,
        "relevance": relevance,
    }


def discover_via_reader(wmr, accounts, per_account_limit):
    """真实发现：逐账号调 MP 后台拉近期文章。"""
    session_cfg = wmr.resolve_session(None)
    if not (session_cfg.get("cookie") and session_cfg.get("token")):
        print("[warn] 未检测到 MP session（cookie/token 为空）。\n"
              "  请先在该机器上跑一次二维码登录：\n"
              "  python3 <reader>/wechat_mp_reader.py session login-start\n"
              "  python3 <reader>/wechat_mp_reader.py session login-status", file=sys.stderr)
        _notify_session_expired("MP session 缺失（cookie/token 为空），请重新登录")
        return []
    # 检查 session 是否有效
    try:
        status = wmr.check_session(session_cfg)
        if not status.get("valid"):
            reason = status.get("reason", "未知原因")
            print(f"[warn] MP session 已失效: {reason}", file=sys.stderr)
            _notify_session_expired(f"MP session 已失效（{reason}），请重新登录")
            return []
    except Exception as e:
        print(f"[warn] session 检查失败: {e}", file=sys.stderr)
    items = []
    for acct in accounts:
        try:
            raw = wmr.list_articles_via_mp_backend(
                acct["fakeid"], session_cfg, count=per_account_limit)
            listed = wmr.extract_article_list(raw)
            for it in listed:
                items.append({
                    "title": it.get("title", ""),
                    "url": it.get("url", ""),
                    "source": acct["name"],
                    "publish_time": it.get("publish_time", ""),
                })
            print(f"[ok] {acct['name']}: 拉到 {len(listed)} 篇")
        except Exception as e:
            print(f"[warn] {acct['name']} 拉取失败: {e}", file=sys.stderr)
    # 如果全部账号都拉到 0 篇，可能是 session 过期
    if not items:
        _notify_session_expired("MP 拉取到 0 篇文章，session 可能已过期，请重新登录")
    return items


def _notify_session_expired(reason):
    """session 过期时通过 Server酱 推送微信提醒。"""
    try:
        from pathlib import Path as _P
        import sys as _sys
        _scripts = _P(__file__).resolve().parent
        if str(_scripts) not in _sys.path:
            _sys.path.insert(0, str(_scripts))
        from notify_wechat import send
        send(
            title="⚠️ MP Session 过期提醒",
            desp=f"## 微信公众平台 Session 过期\n\n**原因**：{reason}\n\n**更新步骤**：\n\n1. 浏览器打开 mp.weixin.qq.com 扫码登录\n2. F12 → Network → 复制 Cookie\n3. 地址栏复制 token 数字\n4. 运行更新命令（详见项目文档）\n\n---\n*此消息由法律周报系统自动推送*",
        )
    except Exception:
        pass  # 推送失败不影响主流程


def mock_items(accounts):
    """不联网的样例，用于验证整条管线。"""
    samples = [
        ("董监高违反勤勉义务的赔偿责任认定", "http://mp.weixin.qq.com/s/mock1", "上海二中院"),
        ("夫妻借款后离婚，债权人要求一方单独出具借条是否为共同债务", "http://mp.weixin.qq.com/s/mock2", "山东高法"),
        ("超龄劳动者工作中受伤能否获得工伤赔偿", "http://mp.weixin.qq.com/s/mock3", "山东高法"),
        ("涉新质生产力企业典型案例全文发布", "http://mp.weixin.qq.com/s/mock4", "上海一中院"),
        ("建工司法解释二条文解读（上）", "http://mp.weixin.qq.com/s/mock5", "上海二中院"),
        ("AI 大模型在合同审查中的落地实践", "http://mp.weixin.qq.com/s/mock6", "上海一中院"),
    ]
    return [{"title": t, "url": u, "source": s, "publish_time": ""} for t, u, s in samples]


def build_candidates(raw_items):
    out = []
    for it in raw_items:
        title = it.get("title", "").strip()
        url = it.get("url", "").strip()
        if not title or not url:
            continue
        feats = heuristic_label(title, it.get("source", ""))
        out.append({
            "title": title,
            "url": url,
            "category": "ai-legal" if any(k.lower() in title.lower() for k in AI_SIGNALS) else "legal",
            "source": it.get("source", ""),
            "features": feats,
            "publish_time": it.get("publish_time", ""),
        })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reader", default=None, help="wechat-mp-reader/scripts 路径")
    ap.add_argument("--out", default=str(BASE / "candidates.jsonl"))
    ap.add_argument("--limit", type=int, default=30, help="每账号拉取篇数")
    ap.add_argument("--mock", action="store_true", help="不联网，用内置样例")
    args = ap.parse_args()

    accounts = load_accounts()
    print(f"[info] 待发现账号: {[a['name'] for a in accounts]}")

    if args.mock:
        print("[info] --mock 模式，使用内置样例")
        raw = mock_items(accounts)
    else:
        wmr = load_reader(args.reader)
        raw = discover_via_reader(wmr, accounts, args.limit)

    candidates = build_candidates(raw)
    out_path = Path(args.out)
    with open(out_path, "w", encoding="utf-8") as f:
        for c in candidates:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    print(f"[done] 写出 {len(candidates)} 条候选 → {out_path}")


if __name__ == "__main__":
    main()
