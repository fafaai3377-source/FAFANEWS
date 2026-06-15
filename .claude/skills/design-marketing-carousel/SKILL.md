---
name: design-marketing-carousel
description: 지난 24시간 AI·디자인·마케팅 아티클을 웹 검색으로 수집해 한국어로 번역·요약하고, Pretendard 기반의 1080×1350 카드 23장(표지 1 + AI 7 + 디자인 7 + 마케팅 7 + 엔딩 1)을 만든 뒤, 카드당 실제 기사 이미지와 클릭 가능한 출처 링크를 포함한 PDF(YYMMDD_FAFA NEWS.pdf)로 출력한다. "디자인 마케팅 브리핑", "FAFA NEWS", "오늘의 디자인 소식 카드" 요청 시 사용.
---

# FAFA NEWS — 디자인·마케팅 모닝 브리핑 캐러셀

매 실행 시 지난 24시간(전날 같은 시각 ~ 지금) 발행 소식을 수집해 한국어로 요약하고,
공유용 카드 21장 + 클릭 가능 링크 PDF를 만든다.

---

## 0. 날짜 확인
```bash
date "+%Y-%m-%d (%a)"
```
모든 검색·표기는 오늘 KST 날짜 기준.

---

## 1. 아티클 수집 전략 — 품질 우선 3단계

### 1-1. 한국 큐레이션 플랫폼 먼저 (1순위)
아래 플랫폼들은 전문가가 선별한 양질의 콘텐츠를 다루며, 동일 소식이 여러 곳에 뜬다면
**그 이슈가 중요하다는 신호** → 해당 주제를 우선 배치한다.

WebSearch로 오늘 날짜 포함하여 검색:
- **서핏(surfit.io)** — AI·디자인·마케팅 트렌드 큐레이션
  - 검색어: `site:surfit.io 오늘날짜` 또는 `surfit.io AI 디자인 마케팅 YYYY-MM-DD`
- **이오플래닛(eopla.net)** — UX·프로덕트·브랜드 전문 뉴스레터
  - 검색어: `site:eopla.net YYYY-MM-DD` 또는 `이오플래닛 디자인 AI YYYY년`
- **브런치(brunch.co.kr)** — 전문가 실무 인사이트·케이스스터디
  - 검색어: `site:brunch.co.kr 디자인 AI 마케팅` (최신순)
- **요즘IT(yozm.wishket.com)** — 개발·디자인·마케팅 트렌드 아티클
- **폴인(folin.co)** — 브랜드·마케팅·조직문화 심층 리포트
- **모비인사이드(mobiinside.co.kr)** — 마케팅·스타트업 실무 인사이트

> 서핏/이오플래닛 글이 원문(영문 기사)을 요약한 경우: **원문 URL을 카드에 사용**하고
> 서핏/이오플래닛을 '경유 큐레이터'로 요약 참고만 한다. 이렇게 하면 og:image 품질이 훨씬 좋다.

### 1-2. 글로벌 전문 매체 (2순위)
- **AI**: TechCrunch · The Verge · VentureBeat · OpenAI Blog · Anthropic Blog · Google Blog · NVIDIA Newsroom
- **디자인**: It's Nice That · Creative Boom · Brand New(underconsideration.com) · Figma Blog · Fast Company Design · Design Week
- **마케팅**: Marketing Dive · MarTech · The Drum · Adweek · Campaign · Digiday

### 1-3. 수집 목표 및 중복 처리 규칙
- 분야별 **7건**씩 수집. 정말 의미 있는 항목이 부족하면 6건으로 줄인다(억지로 채우기 금지).
- **중복 이슈 처리**: 같은 사건이 서핏·이오플래닛·TechCrunch 등 여러 곳에 뜬다면
  → **가장 이미지가 풍부한 원문 URL 1개만 선택** + 요약은 여러 출처를 종합
  → 이 이슈를 해당 카테고리 **상위에 배치** (중복 = 핫토픽 신호)
- 같은 회사의 다른 소식은 2건까지 허용, 같은 행사/릴리즈는 1건만.

---

## 2. 각 항목 확정 정보 (7건 × 3 카테고리)

각 항목에 대해 아래 4가지를 명확히 확정한다:

| 필드 | 기준 |
|------|------|
| **제목** | 한국어 25자 이하, 핵심 키워드 포함 |
| **한국어 요약** | 2~3문장. 사실·수치 중심, 과장 없이. 실무에 왜 중요한지 한 문장 포함 |
| **출처명** | 원문 매체명 (서핏·이오플래닛이 경유라면 원문 매체명) |
| **URL** | **각 항목마다 서로 다른 전용 기사 URL** ← 가장 중요 |

> ⚠️ **URL 품질이 이미지 품질을 결정한다.**
> - og:image가 풍부한 매체 우선: TechCrunch, The Verge, Figma Blog, Fast Company 등
> - roundup/digest URL(여러 소식 모은 페이지) 사용 금지 — og:image가 무관한 이미지로 나옴
> - 같은 URL을 두 카드에 쓰는 것 절대 금지

---

## 3. 환경 준비
```bash
cd /tmp && mkdir -p fonts && cd fonts
npm install pretendard@1.3.9 >/dev/null 2>&1
cp node_modules/pretendard/dist/public/static/*.otf /tmp/fonts/
pip install --quiet Pillow PyMuPDF requests
```

---

## 4. 렌더링 ⚠️ 반드시 저장소의 render_carousel.py 사용

**절대 PDF나 카드 디자인을 즉석에서 새로 만들지 말 것.**
색·레이아웃·폰트는 `render_carousel.py`에 고정돼 있다.

### 교체 절차
1. `render_carousel.py`의 `AI`, `DESIGN`, `MARKETING` 세 리스트만 2단계 결과로 교체.
   - 튜플 형식: `(제목, 한국어요약, 출처명, 원문URL)`
   - 날짜(`DATE_ISO`/`DATE`)는 건드리지 않는다 — 코드가 KST 오늘로 자동 설정.
2. `python3 render_carousel.py` 실행.
3. 산출물: PNG 23장 + PDF `output/YYMMDD_FAFA NEWS.pdf`
   - 이미지 우선순위: ① 기사 og:image → ② Openverse 주제 실사진 → ③ 플레이스홀더

---

## 5. 검증 ⚠️ 이미지·내용 연관성 필수

1. 카드 전체 확인: 이미지가 기사 내용과 직접 연관되는지, 한글 깨짐(□) 없는지.
2. 무관한 이미지나 플레이스홀더 발견 시:
   - 해당 항목 URL을 og:image가 더 좋은 '전용 기사' URL로 교체 후 재렌더.
   - 캐시 문제면 `rm -rf output/_img/해당파일.png` 후 재실행.
3. 통과 후 PDF를 SendUserFile로 공유. 출처 URL 목록도 함께 제공.

---

## 6. Git push → GitHub Actions 렌더링 + 이메일 자동 발송

검증 통과 후 `render_carousel.py`만 커밋·push (PDF/PNG는 커밋 안 함).
GitHub Actions가 인터넷 환경에서 실제 og:image로 렌더링 후 이메일 발송한다.

```bash
cd /home/user/FAFANEWS
git config user.email "fafaai3377@gmail.com"
git config user.name "FAFA NEWS Bot"
git add render_carousel.py
git commit -m "briefing: $(date +'%Y-%m-%d') FAFA NEWS 뉴스 데이터 업데이트"
git push origin main
```

---

## 디자인 시스템 (고정값 — 수정 금지)
- 캔버스 1080×1350, 여백 80px, 폰트 Pretendard
- 표지 배경 파스텔 옐로우 #F7EDA1 / 내지 배경 크림 #FCFAF0
- 메인 차콜 #3A3A3A / 본문 #5A5A56 / 보조 그레이 #929288
- AI 액센트 바이올렛 #6B4EE6 / 디자인 블루 #3A60E8 / 마케팅 코랄 #F06040
- 표지: "Morning Brief" + 날짜 / "FAFA NEWS" / 대형 세로 타이틀 / 요약 카운트
- 내지: 상단 이미지 밴드 620px + 카테고리 pill + 제목·요약·출처·링크
- 구성: 표지 1 + AI 7 + 디자인 7 + 마케팅 7 + 엔딩 1 = 23장

## 자동 실행 (매일 오전 10시 KST)
예약 세션 시작 → 위 1~6단계 수행 → push → GitHub Actions → 이메일 발송.
