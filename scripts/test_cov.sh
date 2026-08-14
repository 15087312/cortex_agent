#!/usr/bin/env bash
# 覆盖率一键测试 + 门禁（阶段阈值由 CI/调用方传入，默认 60）
set -euo pipefail
cd "$(dirname "$0")/.."

MIN=${1:-60}
python -m pytest tests/unit -q -p no:cacheprovider \
  --cov=modules --cov=infra --cov=config --cov=api --cov=utils \
  --cov-report=term-missing --cov-fail-under="$MIN"
