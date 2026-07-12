#!/usr/bin/env bash
# 法律周报 全自动一键运行
#
# 前置（仅需一次）：
#   1. wechat-mp-reader 已装在 <skill_dir>/skills/wechat-mp-reader
#   2. 在本机完成一次 MP 二维码登录：
#        python3 <reader>/wechat_mp_reader.py session login-start
#        python3 <reader>/wechat_mp_reader.py session login-status
#      （用微信扫二维码；session 落盘到 cache/session.json 后免重复）
#
# 用法：
#   bash scripts/run_weekly.sh            # 真实拉取 + 评分 + 出周报
#   bash scripts/run_weekly.sh --mock   # 不联网，用内置样例验证
#
set -e
SKILL_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SKILL_ROOT"
export PYTHONPATH="$SKILL_ROOT/scripts"

echo "▶ Step1: 从法院公众号发现文章 + 自动特征标注"
python3 scripts/discover_mmp.py "$@"

echo "▶ Step2: 评分引擎 + 生成周报"
python3 scripts/run_pipeline.py candidates.jsonl

echo "▶ Step3: 将队列文章按知识库自动导入 IMA（Level 2，按 knowledge_base_id 分库路由）"
if [ -n "$IMA_CLIENT_ID" ] || [ -f /root/.config/ima/client_id ]; then
  python3 scripts/ima_consumer.py || echo "⚠️ IMA 自动入库失败，详见上方错误；队列已保留，下次可重试"
else
  echo "⏭️ 未检测到 IMA 凭证，跳过自动入库（队列保留在 ima_import_queue.jsonl，可手动消费）"
fi

echo "✅ 完成。周报：$(ls -t 周报_*.md 2>/dev/null | head -1 || echo '(见上方输出)')"
