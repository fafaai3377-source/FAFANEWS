#!/bin/bash
# FAFA NEWS 브리핑 — 렌더 실행 엔트리포인트
# 예약 세션/수동 실행 모두 이 스크립트로 산출물(output/)을 생성한다.
set -euo pipefail
cd "$(dirname "$0")"
python3 render_carousel.py
echo "완료 — output/ 에 PNG + PDF 생성됨"
