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

## 이메일 발송
- 수신자: **fafaai3377@gmail.com** (코드 기본값 `send_email.py`의 `DEFAULT_TO`)
- 발송 수단: HTTPS 이메일 API (SMTP 차단 환경). 환경변수 `RESEND_API_KEY` 또는 `SENDGRID_API_KEY` 필요.
- Resend의 경우 **가입 계정 본인 주소(fafaai3377@gmail.com)** 로는 도메인 인증 없이 바로 발송된다.
  → 환경 시크릿에 `RESEND_API_KEY`만 등록하면 매일 자동 발송 완료.
- 다른 주소로 보내려면 도메인 인증 후 `EMAIL_TO`/`EMAIL_FROM` 환경변수로 지정한다.

## 중복 이미지 방지
- 카드 이미지 해시를 추적해 같은 사진은 PDF/카드에 **한 번만** 들어간다.
- 중복 시 해당 카드만 카드별 고유 플레이스홀더로 대체된다.
