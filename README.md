# FAFA NEWS — AI·디자인·마케팅 모닝 브리핑

지난 24시간 AI·디자인·마케팅 소식을 수집·한국어 요약하고, 카드 23장 + 클릭 가능 링크 PDF를 생성한다.

## 구성
- **표지 1 + AI 7 + 디자인 7 + 마케팅 7 + 엔딩 1 = 23장** (1080×1350)
- 디자인 시스템: 파스텔 옐로우(#F7EDA1) + 차콜 + Pretendard
- 분야 액센트: AI 바이올렛 / 디자인 블루 / 마케팅 코랄
- 카드당 실제 기사 이미지(og:image) 자동 삽입, 차단 시 플레이스홀더
- 클릭 가능한 출처 링크 포함 PDF (`YYMMDD_FAFA NEWS.pdf`)

## 실행
```bash
./run_briefing.sh          # 또는: python3 render_carousel.py
```
산출물은 `output/` 에 생성된다(저장소에는 커밋되지 않음).

매 실행 시 `render_carousel.py` 의 `DATE_ISO` / `DATE` / `AI` / `DESIGN` / `MARKETING`
리스트를 당일 수집 결과로 교체한다. 자세한 절차는
`.claude/skills/design-marketing-carousel/SKILL.md` 참고.

## 매일 오전 10시 자동 실행
이 환경은 세션마다 초기화되므로 OS cron이 아니라 **Claude Code 예약 세션(Scheduled trigger)** 으로 돌린다.

1. 의존성은 `.claude/hooks/session-start.sh`(SessionStart 훅)가 세션 시작 시 자동 설치한다
   (Pillow·PyMuPDF·requests + Pretendard). → 이 브랜치를 기본 브랜치에 머지하면 적용됨.
2. Claude Code 웹에서 이 저장소 환경에 **매일 10:00 예약 트리거**를 추가하고,
   프롬프트로 아래를 지정한다:
   ```
   /design-marketing-carousel
   ```
   (또는 "오늘의 디자인 마케팅 브리핑 만들어줘")
3. 예약 세션이 시작되면 스킬이 수집→요약→렌더→PDF→발송을 자동 수행한다.

> 예약 트리거 생성은 웹 UI에서 이뤄진다: https://code.claude.com/docs/en/claude-code-on-the-web
