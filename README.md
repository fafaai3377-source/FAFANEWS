# FAFA NEWS — AI·디자인·마케팅 모닝 브리핑

지난 24시간 AI·디자인·마케팅 소식을 수집·한국어 요약하고, 카드 28장 + 클릭 가능 링크 PDF를 생성한 뒤 이메일로 발송한다.

## 카드 구성

| 섹션 | 장수 |
|------|------|
| 표지 | 1 |
| AI | 7 |
| 디자인 | 12 |
| 마케팅 | 7 |
| 엔딩 | 1 |
| **합계** | **28장** |

- 해상도: 1080×1350px (인스타그램 세로 비율)
- 디자인 시스템: 애플 리퀴드 글래스 글래스모피즘 + Pretendard
- 분야 액센트: AI 바이올렛 / 디자인 블루 / 마케팅 코랄
- 카드당 실제 기사 og:image 자동 삽입 (차단 시 Openverse 검색 이미지 → 그라디언트 플레이스홀더)
- 중복 이미지 해시 추적 — 같은 이미지가 여러 카드에 들어가지 않음
- 클릭 가능한 출처 링크 포함 PDF (`YYMMDD_FAFA NEWS.pdf`)

## 파일 구조

```
FAFANEWS/
├── render_carousel.py        # 메인 렌더러 (뉴스 데이터 + PDF 생성)
├── send_email.py             # 이메일 발송 (Gmail SMTP → Resend → SendGrid)
├── output/                   # 생성된 PNG·PDF (저장소 미커밋)
├── .github/
│   └── workflows/
│       └── daily.yml         # GitHub Actions 자동 빌드·발송 워크플로
└── .claude/
    ├── hooks/
    │   └── session-start.sh  # 세션 시작 시 의존성 자동 설치
    └── skills/
        └── design-marketing-carousel/
            └── SKILL.md      # 브리핑 자동화 스킬 정의
```

## 실행 방법

```bash
python3 render_carousel.py    # PDF 생성 (output/ 에 저장)
python3 send_email.py         # 이메일 수동 발송
```

매 실행 시 `render_carousel.py` 상단의 `AI` / `DESIGN` / `MARKETING` 리스트를
당일 수집 결과로 교체한다. 자세한 절차는 `.claude/skills/design-marketing-carousel/SKILL.md` 참고.

## 자동화 구조

```
Claude Code 세션
  → render_carousel.py 데이터 갱신
  → git push main
  → GitHub Actions (daily.yml)
      → 렌더링 (실제 인터넷 환경 — og:image 정상 수신)
      → send_email.py 실행 → Gmail 발송
```

> Claude Code 원격 컨테이너는 github.com 외 아웃바운드가 차단되어 있어
> 로컬 렌더 시 이미지가 플레이스홀더로 표시된다. 실제 이미지는 GitHub Actions에서만 삽입된다.

## GitHub Actions 설정

`.github/workflows/daily.yml`이 `render_carousel.py` push를 감지해 자동 실행된다.

필요한 GitHub Secrets (`Settings → Secrets → Actions`):

| Secret | 용도 |
|--------|------|
| `GMAIL_APP_PASSWORD` | Gmail SMTP 발송 **(최우선, 설정 권장)** |
| `GMAIL_USER` | 발신 Gmail 주소 (기본값 `fafaai3377@gmail.com`) |
| `RESEND_API_KEY` | Resend API 폴백 발송 |
| `EMAIL_TO` | 수신자 주소 (기본값 `fafaai3377@gmail.com`) |

발송 우선순위: **Gmail SMTP → Resend → SendGrid**

## 뉴스 수집 규칙

- 발송 요일별 수집 기간: **월요일 72시간** (주말 포함), **나머지 요일 24시간**
- 전날과 동일한 기사 재사용 금지
- 링크는 실제 접근 가능한 URL만 사용 (404 시 도메인 루트로 폴백)
- 이미지는 기사 내용과 관련된 실제 이미지만 삽입

## 예약 자동 실행

Claude Code 웹 예약 트리거(`/design-marketing-carousel`)로 매일 오전 실행.
의존성(Pillow·PyMuPDF·requests·Pretendard)은 `.claude/hooks/session-start.sh` SessionStart 훅이 세션 시작 시 자동 설치한다.

참고: https://code.claude.com/docs/en/claude-code-on-the-web
