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
 ("Google I/O 2026: Gemini 3.5 Flash·Spark 공개",
  "구글이 연례 I/O에서 경량 모델 Gemini 3.5 Flash와 범용 AI 에이전트 Gemini Spark를 공개했다. 프런티어급 성능을 1/3 수준 가격에 제공한다고 밝혔다.",
  "CNBC", "https://www.cnbc.com/2026/05/19/google-ai-ultra-gemini-spark-omni.html"),
 ("OpenAI, ChatGPT 광고 매니저 출시",
  "OpenAI가 ChatGPT 안에서 광고를 직접 만들고 운영하는 셀프서브 광고 매니저를 출시했다. 올해 광고 매출 25억 달러를 목표로 한다.",
  "Build Fast with AI", "https://www.buildfastwithai.com/blogs/ai-news-today-may-25-2026"),
 ("Anthropic, 첫 분기 흑자 전망·9000억 달러 밸류에이션",
  "Anthropic이 창사 이래 첫 분기 영업흑자를 전망했다. 300억 달러 펀딩 라운드로 9000억 달러 이상의 기업가치가 거론된다.",
  "Mean.ceo", "https://blog.mean.ceo/ai-advancements-news-may-2026/"),
 ("Anthropic·게이츠 재단, 2억 달러 AI 파트너십",
  "Anthropic과 게이츠 재단이 4년간 2억 달러 규모로 협력한다. 의료·교육·농업 등 소외 지역을 위한 AI 도구 개발이 목표다.",
  "AI News", "https://www.artificialintelligence-news.com/"),
 ("MS·구글·xAI, 출시 전 정부 AI 모델 검증 수용",
  "마이크로소프트·구글·xAI가 출시 전 정부 기관의 AI 모델 안전성 검증을 허용하기로 했다. AI 거버넌스 협력의 분기점으로 평가된다.",
  "CNN Business", "https://www.cnn.com/2026/05/05/tech/microsoft-google-xai-government-test-ai-models"),
 ("Runway, '월드 모델'로 53억 달러 밸류에이션",
  "AI 스타트업 Runway가 영상 학습 기반 '월드 모델'을 차세대 프런티어로 제시했다. 최근 53억 달러 기업가치에 도달했다.",
  "imFounder", "https://imfounder.com/science-tech/ai/ai-updates-may-2026/"),
 ("텔레그램, 메시지 읽고 답하는 어시스턴트 봇 도입",
  "텔레그램이 메시지를 읽고 필터링·응답하는 어시스턴트 봇을 도입한다. AI를 단순 챗봇이 아닌 일상 대화의 보조 레이어로 통합한다.",
  "Medium", "https://medium.com/@davidakpovi/ai-news-week-of-may-18-to-may-24-2026-6cb451ecb766"),
]

DESIGN = [
 ("2026 브랜딩·디자인 트렌드: '감각 디자인'의 부상",
  "정적 자산에서 벗어나 텍스처·깊이·움직임을 더한 감각적 디자인이 부상한다. 플랫폼에 따라 변하는 적응형 아이덴티티 시스템이 올해의 핵심 화두로 떠올랐다.",
  "The Branding Journal", "https://www.thebrandingjournal.com/2026/01/top-branding-design-trends-2026/"),
 ("2026년을 정의할 로고 디자인 트렌드",
  "키네틱 로고와 가변형 워드마크가 주류로 부상하고 있다. 기하학적 정밀함보다 살아 움직이며 상호작용하는 마크가 선호된다.",
  "Creative Bloq", "https://www.creativebloq.com/design/logos-icons/these-logo-design-trends-will-define-2026"),
 ("브랜딩을 살리는 '괴짜다움'",
  "과도하게 다듬어진 미니멀리즘에 대한 반작용으로 개성과 위트를 앞세운 안티브랜드 접근이 주목받는다. 남다름이 차별화의 무기가 됐다.",
  "Creative Boom", "https://www.creativeboom.com/insight/how-being-weird-can-save-branding-in-2026/"),
 ("'의도된 불완전함' — 2026 비주얼 트렌드",
  "AI 시대에 인간적 손맛과 의도된 불완전함을 살린 작업이 새로운 기준으로 떠올랐다. 매끈함보다 날것의 진정성이 강조된다.",
  "Canva Newsroom", "https://www.canva.com/newsroom/news/design-trends-2026/"),
 ("브랜드 아이덴티티를 바꾸는 8가지 흐름",
  "적응형 로고, 접근성 우선 컬러·타이포, 모션 시스템 등 8가지 디자인 흐름이 2026년 브랜드 아이덴티티를 재편하고 있다.",
  "Threerooms", "https://www.threerooms.com/blog/8-design-trends-shaping-brand-identity-in-2026"),
 ("키네틱 로고와 '차일드라이크 아나키'",
  "손글씨·낙서풍의 차일드라이크 아나키와 움직이는 키네틱 로고가 부상한다. 표면적 미니멀리즘의 종말을 예고하는 흐름이다.",
  "Envato Elements", "https://elements.envato.com/learn/logo-and-branding-trends"),
 ("Brand New: 최신 리브랜드·아이덴티티 아카이브",
  "전 세계 주요 로고·아이덴티티 프로젝트를 매일 큐레이션해 업데이트한다. 최신 리브랜드 사례를 한눈에 확인할 수 있는 레퍼런스다.",
  "UnderConsideration", "https://www.underconsideration.com/brandnew/"),
]

MARKETING = [
 ("Spot & Tango, 창사 이래 최대 마케팅 캠페인",
  "D2C 반려견 영양 브랜드가 TV·OOH·필드 마케팅을 아우르는 최대 규모 캠페인을 시작했다. 2026년 마케팅 예산을 50% 늘리고 350만 달러를 TV·OOH에 투입한다.",
  "PetfoodIndustry", "https://www.petfoodindustry.com/pet-food-marketing-and-branding/news/15823276/spot-tango-launches-largest-marketing-campaign-in-company-history"),
 ("Google Marketing Live 2026: 광고의 AI 전환",
  "구글이 AI 기반 캠페인 도구와 검색·쇼핑 내 에이전틱 커머스, 유튜브 성과 스위트 확장을 발표했다. 광고 운영의 자동화가 한층 가속된다.",
  "Google", "https://blog.google/products/ads-commerce/google-marketing-live-2026-collection/"),
 ("틱톡, 신규 광고 포맷 3종 공개",
  "틱톡이 Logo Takeover·Prime Time·TopReach 등 3종 광고 상품을 공개하며 수익화에 박차를 가한다. 문화적 순간을 겨냥한 대형 노출 상품이 핵심이다.",
  "Seafoam Media", "https://seafoammedia.com/may-2026-marketing-news/"),
 ("2026 최고의 마케팅 캠페인 — 월간 리뷰",
  "올해 가장 화제가 된 브랜드 캠페인을 월간으로 정리했다. 창의성과 성과를 동시에 잡은 사례들이 소개된다.",
  "The Gone Network", "https://www.thegonetwork.com/articles/the-best-marketing-campaigns-of-2026---monthly-review-2026"),
 ("스냅, '통합 어트리뷰션' 출시",
  "스냅이 플랫폼 지표와 MMP 데이터를 결합한 통합 어트리뷰션을 출시했다. 앱 광고주가 캠페인을 실시간으로 평가·최적화할 수 있게 됐다.",
  "The Agile Brand Guide", "https://agilebrandguide.com/yesterdays-marketing-technology-ai-news-may-22-2026/"),
 ("4월 베스트 광고 캠페인 15선",
  "4월 한 달간 가장 인상적이었던 글로벌 광고 캠페인 15편을 선정해 소개한다. 크리에이티브 인사이트를 얻기 좋은 모음이다.",
  "Famous Campaigns", "https://www.famouscampaigns.com/2026/05/the-15-best-campaigns-we-saw-in-april/"),
 ("크리에이터 이코노미, 2026년 440억 달러 전망",
  "크리에이터 콘텐츠가 핵심 미디어 채널로 자리잡으며 2026년 총 광고비가 440억 달러에 이를 전망이다. 브랜드의 크리에이터 투자가 본격화된다.",
  "B2the7", "https://www.b2the7.com/news-blog/marketing-trends-may-25-2026"),
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
