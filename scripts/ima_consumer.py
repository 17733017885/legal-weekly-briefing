#!/usr/bin/env python3
"""IMA 待导入队列消费端（OpenAPI 直连，按知识库路由）

读取 ima_import_queue.jsonl（由 ima_importer.py / run_pipeline.py 产出），
按每条自带的 knowledge_base_id 分组，批量调用 IMA OpenAPI import_urls，
将微信公众号文章自动导入对应的独立知识库（进库根目录，省略 folder_id）。

与 ima_importer.py 的关系：
- ima_importer.py：只做"分类决策 + 幂等查重 + 写队列"，不直接调 API（保持可测试、不耦合凭证）
- 本脚本：消费队列，真正发起 OpenAPI 调用；凭证从本地配置文件 / 环境变量读取

鉴权（关键，已实拉校验）：
- Header 名必须是 ima-openapi-clientid / ima-openapi-apikey（openapi 是一个词，不是 open-api）
- knowledge_base_id 必须用 OpenAPI 后端编码 ID（get_addable_knowledge_base_list 返回的 id），
  不能用浏览器 URL 里的 knowledgeBaseId 数字串（否则报 220004 invalid）

幂等：已成功导入的 url 记入 imported_cache.jsonl，再次运行自动跳过。
失败重试：每条最多重试 MAX_ATTEMPTS 次，超限写入 failed_import.jsonl。

用法：
  python3 scripts/ima_consumer.py            # 消费队列并真正导入
  python3 scripts/ima_consumer.py --dry-run  # 只打印分组，不发起请求
"""
import json, time, sys, os, argparse
from pathlib import Path
from urllib import request as ureq
from urllib import error as uerr

BASE = Path(__file__).resolve().parent.parent
QUEUE = BASE / "ima_import_queue.jsonl"
CACHE = BASE / "imported_cache.jsonl"
FAILED = BASE / "failed_import.jsonl"
MAX_ATTEMPTS = 3
BATCH = 10  # IMA 限制单次 1-10 个 url

API_BASE = os.environ.get("IMA_API_BASE", "https://ima.qq.com/openapi/wiki/v1/").rstrip("/") + "/"


def load_credentials():
    """优先读环境变量，否则读本地配置文件。返回 (client_id, api_key)。"""
    cid = os.environ.get("IMA_CLIENT_ID") or _read(_cfg("client_id"))
    key = os.environ.get("IMA_API_KEY") or _read(_cfg("api_key"))
    if not cid or not key:
        raise SystemExit(
            "缺少 IMA 凭证：请设置环境变量 IMA_CLIENT_ID / IMA_API_KEY，"
            "或在 /root/.config/ima/{client_id,api_key} 放置凭证文件。"
        )
    return cid.strip(), key.strip()


def _cfg(name):
    return Path(os.environ.get("IMA_CFG_DIR", "/root/.config/ima")) / name


def _read(p):
    try:
        return p.read_text().strip()
    except Exception:
        return ""


def load_cache():
    if not CACHE.exists():
        return set()
    return set(line.strip() for line in CACHE.read_text().splitlines() if line.strip())


def save_cache(url):
    with CACHE.open("a") as f:
        f.write(url + "\n")


def load_queue():
    if not QUEUE.exists():
        return []
    out = []
    for line in QUEUE.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        rec.setdefault("attempts", 0)
        out.append(rec)
    return out


def call_import(kb_id, urls):
    """调用 import_urls。返回 (ok:bool, results:dict)。网络异常 ok=False。"""
    cid, key = load_credentials()
    body = {"knowledge_base_id": kb_id, "folder_id": "", "urls": urls}
    req = ureq.Request(
        API_BASE + "import_urls",
        data=json.dumps(body).encode(),
        method="POST",
    )
    req.add_header("Content-Type", "application/json")
    req.add_header("ima-openapi-clientid", cid)
    req.add_header("ima-openapi-apikey", key)
    try:
        with ureq.urlopen(req, timeout=30) as r:
            resp = json.loads(r.read().decode())
        if resp.get("code", -1) != 0:
            return False, {"_error": f"code={resp.get('code')} msg={resp.get('msg')}"}
        results = (resp.get("data") or {}).get("results", {})
        return True, results
    except uerr.HTTPError as e:
        return False, {"_error": f"HTTP {e.code}: {e.read().decode()[:200]}"}
    except Exception as e:  # 网络/超时等
        return False, {"_error": str(e)}


def consume(dry_run=False):
    cache = load_cache()
    records = load_queue()
    if not records:
        print("# 队列为空，无需导入。")
        return

    # 去重：已成功导入的 url 直接过滤
    pending = [r for r in records if r.get("url") not in cache]
    if not pending:
        print("# 队列中的 url 均已导入过（命中缓存），清空队列。")
        QUEUE.write_text("")
        return

    # 按 knowledge_base_id 分组
    groups = {}
    for r in pending:
        groups.setdefault(r.get("knowledge_base_id", ""), []).append(r)

    ok_total = 0
    fail_kept = []      # 仍需重试
    fail_final = []      # 超限，移入 failed_import.jsonl

    for kb_id, recs in groups.items():
        if not kb_id:
            # 无知识库 ID（未配置/兜底缺失）→ 直接判失败
            for r in recs:
                r["attempts"] += 1
                r["error"] = "missing knowledge_base_id"
                (fail_final if r["attempts"] >= MAX_ATTEMPTS else fail_kept).append(r)
            continue

        urls = [r["url"] for r in recs]
        label = recs[0].get("category", "") or kb_id
        if dry_run:
            print(f"[dry-run] KB={kb_id} ({label}) -> {len(urls)} 条: {urls}")
            continue

        # 分批
        for i in range(0, len(urls), BATCH):
            batch = urls[i:i + BATCH]
            ok, results = call_import(kb_id, batch)
            if not ok:
                err = results.get("_error", "unknown")
                print(f"  ✗ KB={kb_id} ({label}) 批量失败: {err}", file=sys.stderr)
                for r in recs:
                    if r["url"] in set(batch):
                        r["attempts"] += 1
                        r["error"] = err
                        (fail_final if r["attempts"] >= MAX_ATTEMPTS else fail_kept).append(r)
                continue
            # 逐条解析结果
            for r in recs:
                if r["url"] not in set(batch):
                    continue
                item = results.get(r["url"], {})
                rc = item.get("ret_code", -1)
                if rc == 0:
                    save_cache(r["url"])
                    ok_total += 1
                    print(f"  ✓ {r['url']} -> {label} (media_id={item.get('media_id','')[:24]}...)")
                else:
                    r["attempts"] += 1
                    r["error"] = f"ret_code={rc}"
                    (fail_final if r["attempts"] >= MAX_ATTEMPTS else fail_kept).append(r)
                    print(f"  ✗ {r['url']} ret_code={rc} (attempts={r['attempts']})", file=sys.stderr)

    if not dry_run:
        # 重写队列：只保留仍需重试的条目
        with QUEUE.open("w") as f:
            for r in fail_kept:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        # 超限条目移入 failed_import.jsonl
        if fail_final:
            with FAILED.open("a") as f:
                for r in fail_final:
                    f.write(json.dumps({**r, "ts": time.time()}, ensure_ascii=False) + "\n")
        print(f"# 完成：成功导入 {ok_total} 条；待重试 {len(fail_kept)} 条；永久失败 {len(fail_final)} 条。")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只打印分组，不发起请求")
    args = ap.parse_args()
    consume(dry_run=args.dry_run)
