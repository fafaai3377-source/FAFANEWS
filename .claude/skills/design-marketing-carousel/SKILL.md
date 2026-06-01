---
name: design-marketing-carousel
description: 지난 24시간 AI·디자인·마케팅 아티클을 웹 검색으로 수집해 한국어로 번역·요약하고, Pretendard 기반의 1080×1350 카드 23장(표지 1 + AI 7 + 디자인 7 + 마케팅 7 + 엔딩 1)을 만든 뒤, 카드당 실제 기사 이미지와 클릭 가능한 출처 링크를 포함한 PDF(YYMMDD_FAFA NEWS.pdf)로 출력한다. "디자인 마케팅 브리핑", "FAFA NEWS", "오늘의 디자인 소식 카드" 요청 시 사용.
---

# FAFA NEWS — 디자인·마케팅 모닝 브리핑 캐러셀

매 실행 시 지난 24시간(전날 같은 시각 ~ 지금) 발행 소식을 수집해 한국어로 요약하고,
공유용 카드 21장 + 클릭 가능 링크 PDF를 만든다.

## 0. 날짜 확인
`date "+%Y-%m-%d (%a)"` 로 오늘 날짜/요일 확인. 모든 검색·표기는 이 기준.

## 1. 아티클 수집 (WebSearch)
- AI: 모델·제품 출시, 펀딩·밸류에이션, 정책·거버넌스, 에이전트 → 7건
- 디자인: 프로덕트/UX/그래픽/브랜드 아이덴티티/리브랜드/스튜디오 소식 → 7건
- 마케팅: 브랜드 캠페인/전략/광고/그로스/마테크 → 7건
최근 발행분 우선. 항목별로 제목·출처명·원문 URL·2~3문장 한국어 요약 확보.
의미 있는 항목이 부족하면 억지로 채우지 말고 카드 수를 줄인다(상한 22장).

### 참고 소스(국내외 폭넓게)
네이버 뉴스에만 의존하지 말고 아래 같은 큐레이션·전문 매체도 함께 본다:
- 국내 큐레이션: **서핏(surfit.io)**, **브런치(brunch.co.kr)**, 디자인프레스, 요즘IT(yozm.wishket), 폴인, 까탈로그
- 디자인/브랜드: It's Nice That, Creative Boom, Brand New(UnderConsideration), Fast Company, Figma Blog
- 마케팅: MarTech, Marketing Dive, The Drum, Adweek, 모비인사이드
- AI: TechCrunch, The Verge, VentureBeat, 각 사 공식 블로그(OpenAI·Anthropic·Google 등)
국내 서비스(서핏·브런치)는 해당 글이 인용·링크한 원문 매체 URL을 출처로 쓰는 것이 좋다.

## 2. 한국어 번역·요약
모든 카드 텍스트 한국어. 요약 1~2문장, 사실 중심, 과장 없이. 고유명사는 통용 표기.

## 3. 환경 준비
```bash
# 폰트 (Pretendard) — 다른 CDN은 403일 수 있으니 npm 사용
cd /tmp && mkdir -p fonts && cd fonts
npm install pretendard@1.3.9 >/dev/null 2>&1
cp node_modules/pretendard/dist/public/static/*.otf /tmp/fonts/
# 파이썬 라이브러리
pip install --quiet Pillow PyMuPDF requests
```

## 4. 렌더링
`render_carousel.py` 의 `DATE_ISO`, `DATE`, `DESIGN`, `MARKETING` 리스트만
1단계 결과로 교체한 뒤 `python3 render_carousel.py` 실행.
- 1080×1350 PNG 21장 → `output/`
- 카드당 이미지: ① 기사 og:image/대표 이미지 추출 → ② 차단 시 Openverse에서 주제 관련 실사진 검색 → ③ 그래도 없으면 액센트 플레이스홀더 (최후)
- 클릭 가능한 출처 링크 포함 PDF → `output/YYMMDD_FAFA NEWS.pdf`

## 5. 검증 후 공유
표지 + 카드 1~2장을 열어 한글 깨짐·줄바꿈·이미지·레이아웃 확인 후
PDF와 PNG 전체를 사용자에게 공유(SendUserFile). 출처 URL 목록도 함께 제공.

## 디자인 시스템 (참고 표지 기준 · 고정값)
- 캔버스 1080×1350, 여백 80px, 폰트 Pretendard
- 표지 배경 파스텔 옐로우 #F7EDA1, 내지 배경 크림 #FCFAF0
- 메인 차콜 #3A3A3A, 본문 #5A5A56, 보조 그레이 #929288
- 디자인 액센트 파랑 #3A60E8, 마케팅 액센트 코랄 #F06040
- 표지: 상단 좌 "Morning Brief"+날짜 / 우 "FAFA NEWS", 중앙 좌 대형 세로 타이틀
  (오늘의 / 디자인 / 마케팅 / 브리핑), 하단 좌 요약 카운트 / 우 "넘겨서 보기 →"
- 내지: 상단 이미지 밴드(620px) + 카테고리 pill·인덱스 번호, 하단 제목·요약·출처·링크
- 표지 1 + AI 7 + 디자인 7 + 마케팅 7 + 엔딩 1 = 23장. 항목이 적으면 그만큼 줄인다.
- 엔딩 카드: 표지와 동일한 옐로우+차콜, "오늘 하루도 파이팅!" 메시지

## 자동 실행 (매일 오전 10시)
이 환경은 세션마다 초기화되므로 무인 cron이 아니라 **예약 세션/트리거**로 실행한다.
예약된 세션이 시작되면 위 1~5단계를 그대로 수행하면 된다(스크립트·스킬은 저장소에 영속).
