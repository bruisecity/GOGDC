#!/usr/bin/env bash
# 本地 CI 等价脚本 —— 与 .github/workflows/ci.yml 的 backend + frontend job 同一套命令。
# 用途：push 前或无 runner 环境，在本地跑通同样的检查门禁。
# 用法：bash scripts/ci-local.sh   （在仓库根目录执行）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/backend"

PY=python3

# 依赖环境：优先 venv（建好且 pip 可用）；venv 不可用的环境回退 pip --target
if [ ! -x .venv/bin/python ] || [ ! -x .venv/bin/pip ]; then
  rm -rf .venv
  if $PY -m venv .venv 2>/dev/null && .venv/bin/pip --version >/dev/null 2>&1; then
    .venv/bin/pip install -q -r requirements.txt pytest
  else
    rm -rf .venv
    $PY -m pip install -q --target .ci_deps -r requirements.txt pytest
  fi
fi

run() {  # 选择可用的解释器入口
  if [ -x .venv/bin/python ]; then .venv/bin/python "$@";
  else PYTHONPATH=.ci_deps $PY "$@"; fi
}

echo "==> [1/4] 安装/校验依赖"
run -c "import fastapi, sqlalchemy, pytest; print('deps ok')"

echo "==> [2/4] 编译级检查（compileall）"
run -m compileall -q app tests

echo "==> [3/4] 单元测试（pytest）"
run -m pytest tests -q

echo "==> [4/4] 前端（node:test + vite build）"
cd "$ROOT/frontend"
if [ ! -d node_modules ]; then npm ci; fi
npm test
npm run build

echo "✅ 本地 CI 检查全部通过（等价 GitHub Actions backend + frontend job）"
