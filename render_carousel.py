#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FAFA NEWS — 디자인·마케팅 모닝 브리핑 캐러셀 렌더러
- 표지 1 + 디자인 10 + 마케팅 10 = 21장 (1080x1350 PNG)
- 카드당 실제 기사 이미지(og:image) 1개 자동 삽입
- 클릭 가능한 출처 링크 포함 PDF 출력 (파일명: YYMMDD_FAFA NEWS.pdf)
- 디자인 시스템: 파스텔 옐로우 표지 + 차콜 + Pretendard (참고 표지 기준)

매 실행 시 DATE / DESIGN / MARKETING 리스트만 교체하면 동일 포맷으로 재생성된다.
"""
import os, re, io, html, datetime
import requests
from PIL import Image, ImageDraw, ImageFont, ImageFilter

# ---------------------------------------------------------------- 설정
FONT_DIR = "/tmp/fonts"
OUT      = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
IMG_CACHE= os.path.join(OUT, "_img")
os.makedirs(OUT, exist_ok=True)
os.makedirs(IMG_CACHE, exist_ok=True)

W, H, M = 1080, 1350, 80

# 디자인 시스템 (참고 표지에서 추출)
YELLOW = (247, 237, 161)   # 표지 배경 파스텔 옐로우
CREAM  = (252, 250, 240)   # 내지 배경
INK    = (58, 58, 58)      # 차콜 (메인 타이틀)
INK2   = (90, 90, 86)      # 본문
GRAY   = (146, 146, 136)   # 보조 텍스트
VIOLET = (107, 78, 230)    # AI 액센트
BLUE   = (58, 96, 232)     # 디자인 액센트
CORAL  = (240, 96, 64)     # 마케팅 액센트
LINE   = (230, 228, 216)
WHITE  = (255, 255, 255)

F = {"black":"Pretendard-Black.otf","extrabold":"Pretendard-ExtraBold.otf",
     "bold":"Pretendard-Bold.otf","semibold":"Pretendard-SemiBold.otf",
     "medium":"Pretendard-Medium.otf","regular":"Pretendard-Regular.otf"}
def font(w, s): return ImageFont.truetype(os.path.join(FONT_DIR, F[w]), s)

UA = {"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}

# ---------------------------------------------------------------- 텍스트 헬퍼
def _break_word(d, word, f, mw):
    out, ln = [], ""
    for ch in word:
        if d.textlength(ln + ch, font=f) <= mw: ln += ch
        else:
            if ln: out.append(ln)
            ln = ch
    if ln: out.append(ln)
    return out

def wrap(d, t, f, mw):
    """띄어쓰기 단위 줄바꿈 — 단어가 폭을 넘으면 그 단어만 글자 단위로 분할."""
    out = []
    for para in t.split("\n"):
        ln = ""
        for word in para.split(" "):
            cand = word if not ln else ln + " " + word
            if d.textlength(cand, font=f) <= mw:
                ln = cand
            else:
                if ln: out.append(ln); ln = ""
                if d.textlength(word, font=f) <= mw:
                    ln = word
                else:
                    parts = _break_word(d, word, f, mw)
                    out.extend(parts[:-1]); ln = parts[-1] if parts else ""
        out.append(ln)
    return out

def dl(d, x, y, t, f, fill, mw, gap, maxlines=None):
    a, de = f.getmetrics(); lh = a + de + gap
    lines = wrap(d, t, f, mw)
    if maxlines and len(lines) > maxlines:
        lines = lines[:maxlines]
        while lines and d.textlength(lines[-1] + "…", font=f) > mw:
            lines[-1] = lines[-1][:-1]
        lines[-1] += "…"
    for ln in lines:
        d.text((x, y), ln, font=f, fill=fill); y += lh
    return y

def truncate(d, t, f, mw):
    if d.textlength(t, font=f) <= mw: return t
    while t and d.textlength(t + "…", font=f) > mw: t = t[:-1]
    return t + "…"

def pill(d, x, y, t, f, bg, fg, px=24, py=12):
    tw = d.textlength(t, font=f); a, de = f.getmetrics(); th = a + de
    d.rounded_rectangle([x, y, x+tw+px*2, y+th+py*2], radius=(th+py*2)//2, fill=bg)
    d.text((x+px, y+py-2), t, font=f, fill=fg)
    return x+tw+px*2

# ---------------------------------------------------------------- 이미지 페치
def fetch_og_image(url):
    try:
        r = requests.get(url, headers=UA, timeout=12)
        if r.status_code != 200: return None
        h = r.text
        m = (re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', h, re.I)
             or re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', h, re.I)
             or re.search(r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)', h, re.I))
        if not m: return None
        src = html.unescape(m.group(1)).strip()
        if src.startswith("//"): src = "https:" + src
        elif src.startswith("/"):
            from urllib.parse import urlparse
            p = urlparse(url); src = f"{p.scheme}://{p.netloc}" + src
        ir = requests.get(src, headers=UA, timeout=12)
        if ir.status_code != 200 or len(ir.content) < 1500: return None
        return Image.open(io.BytesIO(ir.content)).convert("RGB")
    except Exception:
        return None

def cover_crop(img, bw, bh):
    iw, ih = img.size
    scale = max(bw/iw, bh/ih)
    nw, nh = int(iw*scale)+1, int(ih*scale)+1
    img = img.resize((nw, nh), Image.LANCZOS)
    x = (nw-bw)//2; y = (nh-bh)//2
    return img.crop((x, y, x+bw, y+bh))

def placeholder(accent, label, bw, bh):
    base = Image.new("RGB", (bw, bh), accent)
    top = Image.new("RGB", (bw, bh), tuple(min(255,c+45) for c in accent))
    mask = Image.linear_gradient("L").resize((bw, bh))
    img = Image.composite(base, top, mask)
    d = ImageDraw.Draw(img)
    f = font("extrabold", 56)
    tw = d.textlength(label, font=f)
    d.text(((bw-tw)//2, bh//2-40), label, font=f, fill=(255,255,255))
    f2 = font("medium", 30)
    sub = "FAFA NEWS"
    d.text(((bw-d.textlength(sub, font=f2))//2, bh//2+40), sub, font=f2, fill=(255,255,255))
    return img

def get_card_image(url, accent, source, bw, bh, key):
    cache = os.path.join(IMG_CACHE, key+".png")
    if os.path.exists(cache):
        return Image.open(cache).convert("RGB")
    img = fetch_og_image(url)
    img = cover_crop(img, bw, bh) if img else placeholder(accent, source, bw, bh)
    img.save(cache)
    return img

# ---------------------------------------------------------------- 카드
def base(bg=CREAM):
    im = Image.new("RGB", (W, H), bg); return im, ImageDraw.Draw(im)

def footer(im, d, idx, total, ac):
    dot, gap = 12, 10; tw = total*dot + (total-1)*gap
    x = (W-tw)//2; y = H-58
    for i in range(total):
        d.ellipse([x, y, x+dot, y+dot], fill=(ac if i == idx-1 else LINE)); x += dot+gap
    f = font("bold", 24)
    d.text((W-M-d.textlength("FAFA NEWS", font=f), y-4), "FAFA NEWS", font=f, fill=GRAY)
    d.text((M, y-4), f"{idx:02d} / {total:02d}", font=f, fill=GRAY)

def cover(date_str, count):
    im, d = base(YELLOW)
    d.text((M, 96), "Morning Brief", font=font("bold", 42), fill=INK)
    d.text((M, 156), date_str, font=font("bold", 42), fill=INK)
    fn = font("bold", 42); d.text((W-M-d.textlength("FAFA NEWS", font=fn), 96), "FAFA NEWS", font=fn, fill=GRAY)
    big = font("black", 150); y = 380
    for ln in ["오늘의", "디자인", "마케팅", "브리핑"]:
        d.text((M, y), ln, font=big, fill=INK); y += 168
    d.text((M, H-118), f"지난 24시간 AI·디자인·마케팅 소식 {count}건", font=font("medium", 32), fill=(120,118,96))
    nav = "넘겨서 보기 →"; fn2 = font("bold", 38)
    d.text((W-M-d.textlength(nav, font=fn2), H-122), nav, font=fn2, fill=INK)
    p = os.path.join(OUT, "01_cover.png"); im.save(p)
    return p, None

def closing(fn):
    im, d = base(YELLOW)
    d.text((M, 96), "Outro", font=font("bold", 42), fill=INK)
    fn0 = font("bold", 42); d.text((W-M-d.textlength("FAFA NEWS", font=fn0), 96), "FAFA NEWS", font=fn0, fill=GRAY)
    big = font("black", 150); y = 430
    for ln in ["오늘", "하루도", "파이팅!"]:
        d.text((M, y), ln, font=big, fill=INK); y += 168
    d.text((M, y+24), "오늘의 브리핑은 여기까지 — 좋은 하루 보내세요.", font=font("medium", 38), fill=(120,118,96))
    d.text((M, H-118), "내일 아침 10시, 다시 만나요", font=font("medium", 32), fill=(120,118,96))
    nav = "FAFA NEWS"; fn2 = font("bold", 38)
    d.text((W-M-d.textlength(nav, font=fn2), H-122), nav, font=fn2, fill=INK)
    p = os.path.join(OUT, fn); im.save(p)
    return p, None

def card(idx, total, cat_en, cat_ko, ac, title, body, source, url, fn):
    im, d = base(CREAM)
    BH = 620
    img = get_card_image(url, ac, source, W, BH, fn.split(".")[0])
    im.paste(img, (0, 0))
    # 이미지 위 그라데이션(가독성) — 상단 살짝 어둡게
    shade = Image.new("RGBA", (W, 180), (0,0,0,0))
    sd = ImageDraw.Draw(shade)
    for i in range(180):
        sd.line([(0,i),(W,i)], fill=(0,0,0,int(120*(1-i/180))))
    im.paste(Image.alpha_composite(im.crop((0,0,W,180)).convert("RGBA"), shade).convert("RGB"), (0,0))
    d = ImageDraw.Draw(im)
    # 카테고리 pill (이미지 위 좌상단)
    label = cat_en if cat_en == cat_ko else f"{cat_en} · {cat_ko}"
    pill(d, M, 40, label, font("bold", 28), ac, WHITE)
    # 본문 영역
    y = BH + 44
    y = dl(d, M, y, title, font("extrabold", 56), INK, W-2*M, 10, maxlines=3); y += 18
    d.rectangle([M, y, M+72, y+7], fill=ac); y += 40
    dl(d, M, y, body, font("regular", 36), INK2, W-2*M, 14, maxlines=4)
    # 출처 + 클릭 가능 링크
    sy = H-176
    d.text((M, sy), "출처", font=font("bold", 26), fill=ac)
    d.text((M+72, sy), source, font=font("semibold", 30), fill=INK)
    ly = sy + 46
    disp = truncate(d, url.replace("https://","").replace("http://",""), font("medium", 26), W-2*M-40)
    d.text((M, ly), "↗ " + disp, font=font("medium", 26), fill=BLUE)
    lw = d.textlength("↗ " + disp, font=font("medium", 26))
    link_rect = (M, ly-4, M+lw, ly+38)   # 픽셀 좌표 (PDF 링크용)
    footer(im, d, idx, total, ac)
    p = os.path.join(OUT, fn); im.save(p)
    return p, (link_rect, url)

# ---------------------------------------------------------------- PDF (클릭 가능 링크)
def build_pdf(pages, pdf_path):
    import fitz
    doc = fitz.open()
    for png, link in pages:
        pg = doc.new_page(width=W, height=H)
        buf = io.BytesIO()
        Image.open(png).convert("RGB").save(buf, format="JPEG", quality=82, optimize=True)
        pg.insert_image(fitz.Rect(0, 0, W, H), stream=buf.getvalue())
        if link:
            rect, url = link
            pg.insert_link({"kind": fitz.LINK_URI, "from": fitz.Rect(*rect), "uri": url})
    doc.save(pdf_path); doc.close()

# ================================================================ 데이터 (매 실행 교체)
# 분야별 7건씩. (제목, 한국어요약, 출처명, 원문 URL)
DATE_ISO = datetime.date(2026, 5, 31)
DATE = "2026년 5월 31일 (일)"

AI = [
 ("Gemini 3.5 Flash 출시 — 프런티어 성능, 1/3 가격",
  "구글 딥마인드가 Gemini 3.5 Flash를 출시했다. 유사 프런티어 모델 대비 최대 4배 빠르고 가격은 1/3 수준으로, 코딩·에이전트 벤치마크에서 Gemini 3.1 Pro를 넘어섰다.",
  "CNBC", "https://www.cnbc.com/2026/05/19/google-ai-ultra-gemini-spark-omni.html"),
 ("Google Gemma 4 오픈 모델 공개 — 아파치 2.0 라이선스",
  "구글이 Gemma 4 패밀리(2B·4B·26B·31B)를 아파치 2.0으로 공개했다. 31B Dense 모델은 글로벌 오픈 모델 리더보드 3위에 올랐다.",
  "WhatLLM", "https://whatllm.org/blog/new-ai-models-may-2026"),
 ("GPT-5.5 Instant, ChatGPT 기본 모델로 교체",
  "OpenAI가 GPT-5.5 Instant를 ChatGPT 기본 모델로 전환했다. 5월 5일 출시된 이 모델은 응답 속도와 일상 대화 능력을 대폭 강화했다.",
  "LLM Stats", "https://llm-stats.com/llm-updates"),
 ("OpenRouter, 시리즈B 1억1300만 달러 조달",
  "AI 모델 라우팅 인프라 스타트업 OpenRouter가 5월 26일 시리즈B로 1억1300만 달러를 유치했다. 에이전트 시대 모델 간 트래픽 분산 수요가 폭발적으로 늘고 있다.",
  "Tech Startups", "https://techstartups.com/2026/05/26/venture-capital-startup-funding-roundup-may-26-2026/"),
 ("CopilotKit, 시리즈A 2700만 달러 — 앱 네이티브 에이전트",
  "앱 내 AI 에이전트 배포 플랫폼 CopilotKit이 Glilot Capital·NFX·SignalFire 주도로 2700만 달러를 유치했다. 기업용 자체 호스팅 제품도 함께 출시했다.",
  "TechCrunch", "https://techcrunch.com/2026/05/05/copilotkit-raises-27m-to-help-devs-deploy-app-native-ai-agents/"),
 ("Anthropic, 밀라노 오피스 오픈 & '광고 없음' 선언",
  "Anthropic이 5월 27일 이탈리아 밀라노에 유럽 사무소를 열었다. 동시에 광고 수익 모델은 AI 조수의 진정성과 양립할 수 없다며 광고 배제 원칙을 공식화했다.",
  "Axios", "https://www.axios.com/2026/05/21/google-ai-anthropic-openai-war"),
 ("2026 VC, AI 에이전트 인프라에 188억 달러 베팅",
  "2026년 VC들은 2025년 이후 창업한 AI 스타트업에 총 188억 달러를 투자했다. 모델 라우팅·멀티 에이전트 워크플로·자율 금융 거버넌스가 투자 집중 영역이다.",
  "Mean CEO", "https://blog.mean.ceo/ai-startup-funding-news-may-2026/"),
]

DESIGN = [
 ("Porto Rocha, 런던 스튜디오 정식 오픈",
  "뉴욕 기반 글로벌 브랜딩 에이전시 Porto Rocha가 런던 스튜디오를 정식 오픈했다. 라틴아메리카 디자인 어워드 '올해의 스튜디오' 수상에 이어 유럽 시장으로 본격 확장한다.",
  "The Brand Identity", "https://the-brandidentity.com/"),
 ("텍스처·온기·촉각적 반란 — 2026 그래픽 디자인 트렌드",
  "2026 디자인은 차갑고 매끈한 AI 미학에 반발해 곡물 질감·손맛·따뜻한 색조를 전면에 내세운다. 진정성 있는 인간적 표현이 최고의 경쟁력으로 부상했다.",
  "Creative Bloq", "https://www.creativebloq.com/design/graphic-design/texture-warmth-and-tactile-rebellion-the-big-graphic-design-trends-for-2026"),
 ("내 스튜디오를 어떻게 브랜딩하나 — Koto·Kiln·Vanderbrand",
  "Koto·Studio Kiln·Vanderbrand 세 스튜디오가 자사 브랜드 아이덴티티 구축 과정을 솔직하게 공유했다. '신뢰 구축'과 '원칙 먼저'가 공통 키워드로 꼽혔다.",
  "The Brand Identity", "https://the-brandidentity.com/insight/how-do-you-actually-brand-your-own-studio-we-asked-koto-studio-kiln-and-vanderbrand"),
 ("'의도된 불완전함' — 2026 비주얼 트렌드 보고서",
  "AI가 완벽한 이미지를 양산하는 시대에 Canva가 '의도된 불완전함'을 2026 핵심 비주얼 언어로 선정했다. 그레인·스캔·낙서·콜라주가 고급 표현으로 재평가된다.",
  "Canva Newsroom", "https://www.canva.com/newsroom/news/design-trends-2026/"),
 ("키네틱 로고와 '차일드라이크 아나키' — 2026 로고 트렌드",
  "로고가 움직이고 대화한다. 기하학적 정밀함 대신 손 그림 느낌의 키네틱 마크가 브랜드 차별화 수단이 됐다. 표면적 미니멀리즘의 종말이 가시화되고 있다.",
  "Creative Bloq", "https://www.creativebloq.com/design/logos-icons/these-logo-design-trends-will-define-2026"),
 ("'이상함'이 브랜딩을 살린다 — 안티브랜드의 부상",
  "과잉 세련된 미니멀리즘에 지친 소비자들이 위트·개성·날것의 감성을 앞세운 브랜드에 열광한다. 'Weird by design'이 2026년 최고의 포지셔닝 전략으로 주목받는다.",
  "Creative Boom", "https://www.creativeboom.com/insight/how-being-weird-can-save-branding-in-2026/"),
 ("폴란드발 글로벌 스튜디오 — Piotr Stala 인터뷰",
  "폴란드 바르샤바에서 국제 브랜딩 스튜디오를 운영하는 Piotr Stala가 지역 기반 글로벌 에이전시의 비결을 공유했다. '위치보다 관점'이 핵심 경쟁력이라고 강조했다.",
  "The Brand Identity", "https://the-brandidentity.com/interview/how-piotr-stala-runs-an-international-branding-studio-from-poland"),
]

MARKETING = [
 ("Spot & Tango, 창사 이래 최대 캠페인 — TV·OOH 350만 달러",
  "D2C 반려견 영양 브랜드가 Paramount·ESPN·Hulu 등 스트리밍·선형 TV와 옥외광고를 아우르는 역대 최대 캠페인을 3단계로 순차 전개한다. 마케팅 예산도 50% 증액했다.",
  "PetfoodIndustry", "https://www.petfoodindustry.com/pet-food-marketing-and-branding/news/15823276/spot-tango-launches-largest-marketing-campaign-in-company-history"),
 ("TikTok, 광고 포맷 3종 신규 출시 — Logo Takeover·Prime Time·TopReach",
  "TikTok이 앱 실행 화면을 브랜드로 도배하는 Logo Takeover, 15분 내 연속 3회 노출 Prime Time, 최대 도달을 위한 TopReach를 동시에 공개했다. 수익화 공세가 본격화됐다.",
  "Seafoam Media", "https://seafoammedia.com/may-2026-marketing-news/"),
 ("Google Marketing Live 2026 — 에이전틱 광고 시대 선언",
  "구글이 Google Marketing Live에서 AI 기반 캠페인 자동화, 검색·쇼핑 내 에이전틱 커머스, 유튜브 성과 스위트 확장을 발표했다. 광고 운영의 AI 전환이 가속화된다.",
  "Google Blog", "https://blog.google/products/ads-commerce/google-marketing-live-2026-collection/"),
 ("e.l.f. 뷰티, CBS 서바이버 50주년 파이널 파트너십",
  "e.l.f.가 CBS 서바이버 50주년 생방송 파이널에 맞춰 대규모 브랜드 파트너십 캠페인을 전개했다. Paramount+와 CBS 동시 방영으로 광범위한 노출 효과를 노렸다.",
  "Brand Vision", "https://www.brandvm.com/post/best-marketing-campaigns-2026"),
 ("Kinder Bueno, 라스베이거스 Sphere 점령 — 'Yes, Bueno' 캠페인",
  "킨더 부에노가 라스베이거스 Sphere 외관에 대형 애니메이션 클로우 머신 영상을 송출하며 Sweets & Snacks Expo 기간 최대 화제를 모았다. 몰입형 OOH의 새 기준을 세웠다.",
  "The Gone Network", "https://www.thegonetwork.com/articles/the-best-marketing-campaigns-of-2026---monthly-review-2026"),
 ("Vaseline, 전통 광고 탈피 — TikTok 소셜 리스닝 전략으로 전환",
  "유니레버 Vaseline이 기존 광고 예산을 소셜 리스닝으로 전환해 TikTok 트렌드와 커뮤니티 대화를 실시간으로 콘텐츠에 반영했다. 브랜드 참여율이 대폭 상승했다.",
  "KNB Comm", "https://www.knbcomm.com/blog/marketing-hits-misses-may-2026"),
 ("크리에이터 이코노미 440억 달러 — 장기 파트너십이 대세",
  "크리에이터 콘텐츠가 핵심 미디어 채널로 격상되며 2026년 총 광고비가 440억 달러에 달할 전망이다. 브랜드들은 단발 협찬 대신 분기 단위 장기 파트너십으로 전략을 전환하고 있다.",
  "B2the7", "https://www.b2the7.com/news-blog/marketing-trends-may-2026-google-ai-tiktok-linkedin"),
]

# (영문 라벨, 한글 라벨, 액센트, 파일 접미사, 기사 리스트)
SECTIONS = [
 ("AI", "AI", VIOLET, "ai", AI),
 ("DESIGN", "디자인", BLUE, "design", DESIGN),
 ("MARKETING", "마케팅", CORAL, "marketing", MARKETING),
]

# ================================================================ 실행
def main():
    pages = []
    n_articles = sum(len(s[4]) for s in SECTIONS)
    total = 1 + n_articles + 1   # 표지 + 기사 + 엔딩
    pages.append(cover(DATE, n_articles))
    idx = 2
    for cat_en, cat_ko, ac, suffix, items in SECTIONS:
        for t, b, s, u in items:
            pages.append(card(idx, total, cat_en, cat_ko, ac, t, b, s, u, f"{idx:02d}_{suffix}.png"))
            idx += 1
    pages.append(closing(f"{idx:02d}_closing.png"))
    pdf_name = DATE_ISO.strftime("%y%m%d") + "_FAFA NEWS.pdf"
    pdf_path = os.path.join(OUT, pdf_name)
    build_pdf(pages, pdf_path)
    print("PNG:", total, "장")
    print("PDF:", pdf_path)
    # 이메일 자동 발송 (RESEND_API_KEY / SENDGRID_API_KEY 설정 시)
    try:
        import send_email
        send_email.send(pdf_path, DATE)
    except Exception as e:
        print("이메일 발송 생략:", e)

if __name__ == "__main__":
    main()
