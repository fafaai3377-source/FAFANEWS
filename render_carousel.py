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
DATE_ISO = datetime.date(2026, 5, 30)
DATE = "2026년 5월 30일 (토)"

AI = [
 ("Anthropic, 사이버보안 특화 모델 'Mythos' 공개",
  "Anthropic이 보안 분야에 특화된 최신 모델 Mythos를 제한적 파트너에 공개했다. 사이버 리스크 평가를 위해 광범위한 배포는 유보했다.",
  "QverLabs", "https://qverlabs.com/blog/openai-vs-google-deepmind-vs-anthropic-2026"),
 ("OpenAI, 역대 최대 민간 펀딩 $1,220억 클로징",
  "OpenAI가 기업가치 8,520억 달러로 1,220억 달러 펀딩을 완료했다. CFO Sarah Friar는 2026년 하반기 IPO 시 일반 투자자 배정을 예고했다.",
  "Fladgate", "https://www.fladgate.com/insights/ai-round-up-may-2026"),
 ("DeepMind, AGI 도달 시점 '2029년'으로 앞당겨",
  "Demis Hassabis가 AGI 타임라인을 기존 5~10년에서 2029년으로 단축했다. AlphaProof Nexus가 미해결 에르되시 문제 9개를 풀어낸 것이 배경이다.",
  "Crescendo AI", "https://www.crescendo.ai/news/latest-ai-news-and-updates"),
 ("Gemini 3.5 Flash 정식 출시 — 프런티어급 성능 4배 빠르게",
  "Google의 Gemini 3.5 Flash가 GA 상태로 출시됐다. 동급 모델 대비 4배 빠른 속도에 100만 토큰 컨텍스트를 $1.50/$9 per 1M 토큰으로 제공한다.",
  "LLM Stats", "https://llm-stats.com/ai-news"),
 ("OpenAI, 컨설팅 자회사 'DeployCo' $40억 조달",
  "OpenAI가 기업 AI 배포 전문 자회사 DeployCo를 출범하며 TPG·Bain Capital 등 19개사에서 40억 달러를 모았다. 엔터프라이즈 시장 공략이 본격화된다.",
  "Mean.ceo", "https://blog.mean.ceo/ai-startup-funding-news-may-2026/"),
 ("ByteDance, AI 인프라에 $700억 투자 계획",
  "바이트댄스가 2026년 데이터센터·AI 인프라 투자 규모로 최대 700억 달러를 검토 중이다. 글로벌 AI 패권 경쟁에서 자체 인프라 확보에 집중한다.",
  "imFounder", "https://imfounder.com/science-tech/ai/ai-updates-may-2026/"),
 ("Google, $400억으로 Anthropic 투자 최종 확정",
  "구글이 Anthropic에 대한 400억 달러 전략적 투자를 최종 확정했다. AI 모델 공급망의 핵심 거점을 확보하려는 구글의 장기 포석으로 평가된다.",
  "Trending Topics EU", "https://www.trendingtopics.eu/google-bets-40-billion-on-anthropic-in-landmark-ai-power-play/"),
]

DESIGN = [
 ("Figma, AI 코딩 에이전트 연결 'Figma Make' 출시",
  "Figma가 5월 28일 로컬 코드베이스와 AI 코딩 에이전트를 연결하는 Figma Make를 선보였다. 디자인 프롬프트만으로 코드 편집이 가능해졌다.",
  "Figma Release Notes", "https://www.figma.com/release-notes/"),
 ("Google I/O 2026: AI 디자인 앱 'Pics' 공개",
  "구글이 I/O에서 텍스트 프롬프트로 소셜 그래픽·마케팅 자료를 생성하는 Workspace용 AI 앱 Pics를 발표했다. 디자인 전문 지식 없이도 활용 가능하다.",
  "TechCrunch", "https://techcrunch.com/2026/05/19/ai-design-tools-are-the-next-big-battleground-and-google-is-going-all-in-at-io-2026/"),
 ("2026 UX/UI 트렌드: '고요한 인터페이스·투명한 AI'",
  "불필요한 시각적 화려함을 걷어낸 고요한 인터페이스와 AI 개입 여부를 솔직하게 드러내는 투명한 AI 디자인이 올해 UX의 핵심 흐름이다.",
  "Envato Elements", "https://elements.envato.com/learn/ux-ui-design-trends"),
 ("2026년을 정의할 로고 트렌드: 키네틱·가변형 워드마크",
  "움직이는 키네틱 로고와 플랫폼에 따라 변하는 가변형 워드마크가 주류로 부상하고 있다. 기하학적 정밀함보다 살아 숨쉬는 아이덴티티가 선호된다.",
  "Creative Bloq", "https://www.creativebloq.com/design/logos-icons/these-logo-design-trends-will-define-2026"),
 ("'기이함'이 브랜딩을 구원한다",
  "과잉 정제된 미니멀리즘에 대한 반작용으로 개성·위트·안티브랜드적 접근이 주목받는다. 2026년엔 남다른 기이함 자체가 차별화의 핵심 무기다.",
  "Creative Boom", "https://www.creativeboom.com/insight/how-being-weird-can-save-branding-in-2026/"),
 ("Figma Buzz: 캠페인 에셋 수백 종 일괄 편집 기능 추가",
  "Figma Buzz가 스프레드시트 업로드 한 번으로 수백 가지 에셋 변형을 생성하는 대량 편집 기능을 추가했다. 마케팅 팀의 에셋 제작 속도가 크게 높아진다.",
  "Figma Release Notes", "https://www.figma.com/release-notes/"),
 ("최고의 리브랜드는 '급진적이지 않다'",
  "성공적인 리브랜드는 전면 교체가 아니라 기존 아이덴티티를 세련되게 진화시키는 방식임을 Creative Bloq가 분석했다. 과감함보다 일관성이 신뢰를 만든다.",
  "Creative Bloq", "https://www.creativebloq.com/design/branding/why-the-best-rebrands-arent-the-most-radical"),
]

MARKETING = [
 ("OpenAI, ChatGPT 내 CPC 광고 론칭 — 2026 광고 매출 $25억 목표",
  "OpenAI가 ChatGPT 안에 클릭당 과금(CPC) 방식의 광고를 도입했다. 올해 광고 수익 목표를 25억 달러로 제시하며 AI 광고 플랫폼 경쟁에 본격 합류했다.",
  "Ad Age", "https://adage.com/"),
 ("Google Marketing Live 2026: 에이전트 광고·유튜브 성과 강화",
  "구글이 검색·쇼핑 내 에이전틱 커머스와 새로운 AI 캠페인 도구, 유튜브 성과 스위트 확장을 발표했다. 광고 집행의 자동화가 한층 가속된다.",
  "Google Blog", "https://blog.google/products/ads-commerce/google-marketing-live-2026-collection/"),
 ("Meta, 미국 디지털 광고 수익 구글 첫 추월 전망",
  "2026년 Meta가 미국 디지털 광고 시장에서 처음으로 구글을 앞설 것으로 예측됐다. 크리에이터 인접 인벤토리 확대가 결정적 요인이다.",
  "Marketing Dive", "https://www.marketingdive.com/news/marketing-predictions-for-2026/809124/"),
 ("틱톡, Q2 광고 수익 전년비 +53% 급성장",
  "틱톡의 2026년 2분기 광고 수익이 전년 동기 대비 53% 급증할 것으로 예측됐다. 기존 플랫폼의 점유율 이탈 없이 시장 전체 규모를 키우고 있다.",
  "Seafoam Media", "https://seafoammedia.com/may-2026-marketing-news/"),
 ("크리에이터 이코노미, '핵심 미디어 채널'로 격상 — $440억 전망",
  "주요 광고주들이 크리에이터 콘텐츠를 공식 핵심 미디어 채널로 지정했다. 2026년 크리에이터 광고비는 440억 달러에 달할 전망이다.",
  "B2the7", "https://www.b2the7.com/news-blog/marketing-trends-may-25-2026"),
 ("코카콜라, Gen Z 겨냥 'Share a Coke' 2026 리런치",
  "코카콜라가 QR 코드로 개인화 영상·밈을 생성하는 Gen Z 맞춤형 'Share a Coke' 캠페인을 부활시켰다. 참여형 경험이 핵심 차별화 전략이다.",
  "Story Chief", "https://storychief.io/blog/recent-innovative-marketing-campaigns"),
 ("하인즈 'Looks Familiar': 로고 없이 브랜드를 연상시키다",
  "하인즈가 로고 없이도 형태와 색상만으로 브랜드를 즉각 연상시키는 'Looks Familiar' 캠페인을 선보였다. 브랜드 자산의 힘을 역설적으로 증명한 사례다.",
  "BrandClickX", "https://brandclickx.com/innovative-marketing-campaigns/"),
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
