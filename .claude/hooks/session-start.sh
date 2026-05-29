#!/bin/bash
# FAFA NEWS 브리핑 — 세션 시작 시 렌더링 의존성 준비 (폰트 + 파이썬 라이브러리)
set -euo pipefail

# 원격(웹) 환경에서만 실행
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

# 1) 파이썬 라이브러리
pip install --quiet Pillow PyMuPDF requests >/dev/null 2>&1 || true

# 2) Pretendard 폰트 (CJK 렌더링용) — /tmp/fonts 에 설치
if ! ls /tmp/fonts/Pretendard-Black.otf >/dev/null 2>&1; then
  mkdir -p /tmp/fonts
  ( cd /tmp/fonts && npm install pretendard@1.3.9 >/dev/null 2>&1 \
    && cp node_modules/pretendard/dist/public/static/*.otf /tmp/fonts/ ) || true
fi

echo "FAFA NEWS 환경 준비 완료 (Pillow/PyMuPDF/requests + Pretendard)"
