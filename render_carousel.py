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
DATE_ISO = datetime.date(2026, 5, 29)
DATE = "2026년 5월 29 (금)"

AI = [
 ("Cursor, 500억 달러 밸류에이션에 20억 달러 펀딩 추진",
  "AI 코딩 도구 Cursor를 만든 Anysphere가 a16z·Thrive Capital 주도로 20억 달러 라운드를 협상 중이다. 창업 3년 만에 ARR 20억 달러를 달성한 B2B SaaS 역사상 최단기 성장이다.",
  "The Next Web", "https://thenextweb.com/news/cursor-anysphere-2-billion-funding-50-billion-valuation-ai-coding"),
 ("NVIDIA, Nemotron 3 오픈 모델 패밀리 공개",
  "NVIDIA가 에이전틱 AI 개발을 위한 Nemotron 3 오픈 모델·데이터·라이브러리 패밀리를 발표했다. Accenture·Cursor·Palantir 등 주요 기업들이 조기 채택을 선언했다.",
  "NVIDIA Newsroom", "https://nvidianews.nvidia.com/news/nvidia-debuts-nemotron-3-family-of-open-models"),
 ("GitHub Copilot, 6월 1일부터 토큰 사용량 과금 전환",
  "GitHub Copilot이 무제한 정액제에서 토큰 소비량 기반 과금으로 6월 1일 전환한다. 폭발적 추론 비용을 기존 구독 모델로 감당하지 못한 결과다.",
  "GitHub Blog", "https://github.blog/news-insights/company-news/github-copilot-is-moving-to-usage-based-billing/"),
 ("마이크로소프트, 사내 Claude Code 라이선스 회수",
  "마이크로소프트가 5월 14일부터 임직원 Claude Code 라이선스를 회수하기 시작했다. 직원들의 높은 활용도가 연간 AI 예산을 불과 몇 달 만에 소진시켰기 때문이다.",
  "CyberNews", "https://cybernews.com/ai-news/microsoft-claude-code-burn-yearly-ai-budget/"),
 ("MS 365 Copilot Wave 3: Claude 기본 탑재·Cowork 에이전트 출시",
  "Microsoft 365 Copilot Wave 3가 Anthropic Claude를 Excel·PowerPoint 기본 모델로 추가하고 다단계 업무를 위임하는 Copilot Cowork 기능을 공개했다.",
  "Windows Central", "https://www.windowscentral.com/artificial-intelligence/microsoft-copilot/microsoft-365-copilot-wave-3-announcement"),
 ("Nscale, 유럽 최대 AI 인프라 라운드 — 20억 달러 시리즈 C",
  "클라우드 AI 인프라 스타트업 Nscale이 146억 달러 밸류에이션에 20억 달러 시리즈 C를 마감했다. 유럽 AI 스타트업 역사상 최대 규모 펀딩 라운드로 기록됐다.",
  "crescendo.ai", "https://www.crescendo.ai/news/latest-vc-investment-deals-in-ai-startups"),
 ("ByteDance, 2026년 AI 데이터센터에 최대 700억 달러 투자 논의",
  "TikTok 모기업 ByteDance가 2026년 데이터센터·AI 인프라 투자로 최대 700억 달러를 검토 중인 것으로 알려졌다. 미국 빅테크와 어깨를 나란히 하는 대규모 AI 인프라 확장이다.",
  "LLM Stats", "https://llm-stats.com/ai-news"),
]

DESIGN = [
 ("Every Man Jack 리브랜드 — '노력이 곧 이미지다'",
  "남성 그루밍 브랜드 Every Man Jack이 베이커 메이필드와 함께 '노력하는 남자' 철학의 전면 리브랜드를 단행했다. 연간 애슬릿 파트너십과 프리미엄 컬렉션 출시가 동반된다.",
  "DesignRush", "https://news.designrush.com/every-man-jack-baker-mayfield-effort-over-image-rebrand"),
 ("AI 디자인 도구, 실제 컴포넌트 라이브러리에서 즉시 배포 가능한 UI 생성",
  "2026년 AI 디자인 도구는 팀의 실제 디자인 시스템을 학습해 프로덕션 품질 UI를 즉시 생성하는 수준에 도달했다. 제네릭 목업 시대는 끝나고 바로 쓸 수 있는 결과물 시대가 왔다.",
  "UXPin", "https://www.uxpin.com/studio/blog/ui-ux-design-trends/"),
 ("디자인 시스템, 문서에서 AI 거버넌스 플랫폼으로 진화",
  "2026년 디자인 시스템은 참고 문서를 넘어 AI 생성 인터페이스에도 규칙을 강제하는 거버넌스 플랫폼이 됐다. 인간과 AI 양쪽 산출물을 일관되게 통제하는 것이 핵심이다.",
  "UXPin Studio", "https://www.uxpin.com/studio/blog/ui-ux-design-trends/"),
 ("2026 UX 핵심 흐름: '조용한 인터페이스'와 시각적 허세의 종말",
  "정보 과잉에 지친 사용자를 위해 크고 여백이 넉넉하며 차분한 인터페이스가 2026년 UX의 중심이 됐다. 시각적 화려함 대신 명확성과 신뢰를 우선시하는 흐름이다.",
  "Envato Elements", "https://elements.envato.com/learn/ux-ui-design-trends"),
 ("좋은 AI UX의 기준: 자동조종이 아닌 사려 깊은 조력자",
  "2026년 좋은 AI UX는 모든 것을 자동화하지 않고 사용자가 원할 때만 등장하는 '선택적·투명한 조력자'로 재정의됐다. AI 기능을 강제가 아닌 제안으로 설계하는 것이 핵심 원칙이다.",
  "Ascedia", "https://www.ascedia.com/insights/what-good-ai-ux-actually-looks-like-in-2026"),
 ("2026 브랜딩 트렌드: 진정성·개성·소리치는 아이덴티티",
  "강한 색 대비, 두꺼운 서체, 텍스처 레이어 등 '실제로 만들어진 느낌'의 표현적 아이덴티티가 2026년 브랜딩 주류로 떠올랐다. 템플릿 미니멀리즘에 대한 반작용이다.",
  "The Brand Strategy Lab", "https://thebrandstrategylab.com/blog/branding-trends-were-going-to-see-this-year/"),
 ("UX 설계 패러다임 전환: 비주얼 트렌드에서 성과 중심으로",
  "2026년 UX 트렌드가 시각적 미학 중심에서 명확성·신뢰·적응성·효율을 기준으로 하는 성과 중심 설계로 이동했다. 디자이너 역할도 '화면 제작자'에서 '규칙 시스템 설계자'로 진화 중이다.",
  "CodeWave", "https://codewave.com/insights/ux-design-trends-future/"),
]

MARKETING = [
 ("Meta, 사상 첫 글로벌 디지털 광고 1위 등극 전망 ($2,430억)",
  "eMarketer가 Meta의 2026년 글로벌 디지털 광고 매출이 2,430억 달러로 구글(2,395억)을 사상 처음 추월할 것으로 예측했다. 디지털 광고 역사상 구글이 1위를 내주는 첫 번째 해다.",
  "The Next Web", "https://thenextweb.com/news/meta-surpass-google-digital-ad-revenue-emarketer-2026"),
 ("구글, 2026년 두 번째 코어 업데이트 롤아웃 시작",
  "구글이 5월 2026 코어 알고리즘 업데이트를 롤아웃하기 시작했다. 완전 반영까지 최대 2주가 걸려 SEO 담당자들의 면밀한 모니터링이 필요하다.",
  "Seafoam Media", "https://seafoammedia.com/may-2026-marketing-news/"),
 ("크리에이터 상시 파트너십이 일회성 캠페인을 대체하다",
  "브랜드들이 인플루언서와의 단발성 포스팅 계약에서 멀티 쿼터 장기 파트너십으로 전환하고 있다. 크리에이터를 일관된 브랜드 채널로 활용하는 전략이 마케팅 주류로 자리잡았다.",
  "Seafoam Media", "https://seafoammedia.com/may-2026-marketing-news/"),
 ("Meta Advantage+ AI 자동화가 광고 성장 24.1% 이끌어",
  "Meta의 AI 광고 자동화 시스템 Advantage+가 집행 효율을 높이며 광고 성장률을 24.1%로 끌어올렸다. 반면 구글의 광고 성장률은 11.9%에 그쳐 두 플랫폼의 격차가 벌어지고 있다.",
  "AdWeek", "https://www.adweek.com/media/meta-is-quietly-becoming-a-bigger-ad-business-than-google/"),
 ("릴라이언스 Campa, 인도 4위 탄산음료 브랜드로 부상",
  "릴라이언스 컨슈머의 Campa가 FY26 매출 4,700억 루피를 돌파해 인도 4위 탄산음료 브랜드가 됐다. 공격적 D2C 전략과 가격 경쟁력이 폭발적 성장을 이끌었다.",
  "BestMediaInfo", "https://bestmediainfo.com/mediainfo/advertising/top-advertising-marketing-and-media-news-headlines-of-today-may-29-2026-11885291"),
 ("YouTube, 인쇄 광고로 OTT·전통 TV와 플랫폼 전쟁 선전포고",
  "유튜브가 인쇄 광고를 내세워 OTT 스트리머·전통 TV와의 미디어 플랫폼 주도권 경쟁을 공개 선언했다. 글로벌 광고 플랫폼 전쟁이 새로운 국면으로 접어들었다.",
  "BestMediaInfo", "https://bestmediainfo.com/mediainfo/advertising/top-advertising-marketing-and-media-news-headlines-of-today-may-29-2026-11885291"),
 ("Google Marketing Live 2026: AI 에이전트 쇼핑·광고 전자동화 선언",
  "구글이 AI 기반 캠페인 도구 확장과 검색·쇼핑 내 에이전틱 커머스, 유튜브 성과 스위트 강화를 발표했다. 광고 기획·집행·최적화 전 과정의 AI 자동화가 본격 가속된다.",
  "Google Blog", "https://blog.google/products/ads-commerce/google-marketing-live-2026-collection/"),
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

if __name__ == "__main__":
    main()
