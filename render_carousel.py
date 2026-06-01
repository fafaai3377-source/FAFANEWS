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
DATE_ISO = datetime.date(2026, 6, 1)
DATE = "2026년 6월 1일 (월)"

AI = [
 ("OpenAI, GPT-5.5 공식 출시",
  "OpenAI가 역대 가장 강력한 모델 GPT-5.5를 출시했다. 코딩·연구·데이터 분석에서 성능이 크게 개선됐으며 에이전틱 코딩과 컴퓨터 사용 능력이 특히 뛰어나다.",
  "OpenAI", "https://openai.com/index/introducing-gpt-5-5/"),
 ("Anthropic, Claude Opus 4.8 업데이트",
  "Anthropic이 플래그십 모델 Claude Opus 4.8을 업데이트했다. 코딩·에이전틱 작업·추론 능력이 향상됐으며 병렬 서브에이전트를 활용하는 동적 워크플로를 지원한다.",
  "Dentro.de AI", "https://dentro.de/ai/news/"),
 ("DeepSeek, V4-Pro·Flash 동시 출시",
  "DeepSeek이 100만 토큰 컨텍스트 윈도우를 갖춘 MoE(혼합 전문가) 모델 V4-Pro와 V4-Flash를 동시에 출시했다. 하이브리드 어텐션으로 추론 비용을 낮추고 장기 에이전틱 작업을 최적화했다.",
  "LLM Stats", "https://llm-stats.com/llm-updates"),
 ("GitHub Copilot, AI 크레딧 과금으로 전환",
  "GitHub이 6월 1일부터 Copilot 과금 방식을 요청 기반에서 사용량 기반 'AI 크레딧' 체계로 전환했다. 사용한 만큼 지불하는 구조로 기업 고객의 비용 예측 가능성을 높인다.",
  "OpenAI Release Notes", "https://releasebot.io/updates/openai"),
 ("OpenAI, IPO 기밀 제출 시작",
  "OpenAI가 골드만삭스·모건스탠리를 주관사로 선정하고 IPO 기밀 서류를 제출했다. 9월 상장이 유력하며 밸류에이션은 $8,520억으로 추산된다.",
  "Dentro.de AI", "https://dentro.de/ai/news/"),
 ("OpenRouter, 시리즈B $1.13억 조달",
  "AI 모델 통합 플랫폼 OpenRouter가 시리즈B에서 1억 1,300만 달러를 조달하며 기업가치 13억 달러를 달성했다. 현재 주당 25조 토큰을 처리하는 규모로 성장했다.",
  "Dentro.de AI", "https://dentro.de/ai/news/"),
 ("MIT 연구: 기업 85%, 3년 내 에이전틱 전환 목표",
  "MIT 연구에 따르면 기업의 85%가 3년 안에 에이전틱 AI를 도입하길 원하지만 76%는 인프라 지원이 부족하다. AI 에이전트 확산의 최대 병목은 기술이 아닌 조직 역량이라는 결론이다.",
  "Dentro.de AI", "https://dentro.de/ai/news/"),
]

DESIGN = [
 ("Figma Config 2026, 6월 23-25일 샌프란시스코 개최",
  "제품 크리에이터를 위한 연례 컨퍼런스 Figma Config 2026이 샌프란시스코에서 열린다. AI 기반 디자인 시스템 생성과 프로토타입 자동화 등 대형 발표가 예고되고 있다.",
  "Mean.ceo", "https://blog.mean.ceo/design-trends-june-2026/"),
 ("UX London 2026 개막 — 6월 2일부터 4일간",
  "유럽 최대 UX 컨퍼런스 UX London 2026이 6월 2일 개막한다. 에이전틱 인터페이스 설계·AI 시대의 인간 중심 UX가 핵심 주제로 다뤄진다.",
  "UIUX Trend", "https://uiuxtrend.com/events/"),
 ("'디자인 as 인프라' — AI 시대의 새로운 패러다임",
  "브랜드 규칙을 PDF가 아닌 머신리더블 마크다운 파일로 전환하는 흐름이 가속되고 있다. AI가 실시간으로 브랜드 기준을 적용해 비전문가도 일관된 디자인 산출물을 만들 수 있게 됐다.",
  "Mean.ceo", "https://blog.mean.ceo/design-trends-june-2026/"),
 ("Motion-First 브랜딩, 2026 핵심 트렌드로 부상",
  "정적 로고에서 키네틱·적응형 시스템으로 이동하는 Motion-First 브랜딩이 2026년 디자인의 핵심 흐름으로 자리잡았다. 모바일·소셜·몰입형 환경 모두에 최적화된 다이나믹 비주얼이 표준이 됐다.",
  "Big Orange Planet", "https://www.bigorangeplanet.com/2026/05/20/top-10-branding-trends-in-2026"),
 ("비디자이너가 디자인 운영자로 — AI 브리프의 힘",
  "구조화된 요구사항 중심 브리프로 PM·창업자·마케터가 전문 디자이너 없이도 제품급 UI를 만들고 있다. 2시간 내 클릭 가능한 프로토타입 완성이 새로운 기준이 됐다.",
  "Mean.ceo", "https://blog.mean.ceo/design-trends-june-2026/"),
 ("접근성이 '기능'에서 '인프라'로 재정의",
  "2026 UX/UI 설계에서 접근성은 선택 사항이 아닌 기반 시설로 취급되기 시작했다. 시각·운동·인지·환경 다양성을 기본값으로 전제하는 설계 원칙이 업계 표준으로 정착하고 있다.",
  "UXPin", "https://www.uxpin.com/studio/blog/ui-ux-design-trends/"),
 ("Raw & Imperfect — AI 미학의 반작용",
  "AI가 쏟아내는 매끈한 이미지에 대한 반작용으로 거친 타이포·불균형 레이아웃·스캔 텍스처 등 의도된 불완전함이 새로운 크리에이티브 기준으로 떠올랐다.",
  "Creative Boom", "https://www.creativeboom.com/insight/how-being-weird-can-save-branding-in-2026/"),
]

MARKETING = [
 ("Meta, 글로벌 광고 매출 구글 첫 추월 전망",
  "Meta가 2026년 광고 매출 2,434억 달러로 구글(2,395억)을 처음으로 앞지를 것으로 전망된다. Reels와 Advantage+ 자동화가 24.1% 성장을 견인하며 디지털 광고 지형을 재편하고 있다.",
  "AdWeek", "https://www.adweek.com/media/meta-is-quietly-becoming-a-bigger-ad-business-than-google/"),
 ("ChatGPT 광고 미국 전면 개방 — 최소 예산 없음",
  "OpenAI가 ChatGPT 광고를 미국 내 모든 사업자에게 최소 예산 없이 개방했다. 하루 25억 건의 프롬프트를 기반으로 한 맥락적 타겟팅으로 출시 6주 만에 연환산 1억 달러를 달성했다.",
  "Workshop Digital", "https://www.workshopdigital.com/blog/what-to-know-about-chatgpt-ads/"),
 ("Google Marketing Live 2026: 에이전틱 광고 시대 개막",
  "구글이 Gemini 기반 통합 에이전트 'Ask Advisor'를 공개했다. Google Ads·Analytics·Merchant Center를 단일 인터페이스로 통합해 캠페인 전략부터 실행까지 AI가 자동화한다.",
  "PPC Land", "https://ppc.land/google-marketing-live-2026-every-announcement-that-actually-matters/"),
 ("구글 5월 코어 업데이트, AI 검색 인용 판도 재편",
  "구글의 5월 코어 업데이트로 AI 개요 인용에서 상위 10개 사이트의 점유율이 76%에서 38%로 급락했다. 백링크보다 명확하고 인용 가능한 콘텐츠 구조가 AI 검색 노출의 핵심이 됐다.",
  "Discovered Labs", "https://discoveredlabs.com/blog/google-ai-mode-may-2026-search-update"),
 ("AI 어트리뷰션 갭 심화 — 마케터의 새로운 과제",
  "구글이 AI 개요 클릭 추적 신호를 제거하면서 상위 3위 페이지의 CTR이 18~34% 하락했다. AI 어시스턴트가 구매 결정에 영향을 미치지만 마지막 클릭 모델에서는 보이지 않아 ROI 측정에 구멍이 생겼다.",
  "ROI Revolution", "https://roirevolution.com/blog/ai-search-google-io-2026/"),
 ("FIFA 월드컵 2026 — 브랜드 활성화 전쟁 시작",
  "FIFA 월드컵을 앞두고 글로벌 브랜드의 문화 연결형·팬덤 드리브 체험 마케팅이 본격화됐다. 단순 노출보다 참여와 감정적 연결을 강조한 활성화 전략이 2026년 캠페인의 새 기준으로 자리잡고 있다.",
  "BrandClickX", "https://brandclickx.com/innovative-marketing-campaigns/"),
 ("크리에이터 이코노미, 2026년 광고비 440억 달러 전망",
  "크리에이터 콘텐츠가 핵심 미디어 채널로 자리잡으며 2026년 크리에이터 생태계 총 광고비가 440억 달러에 이를 전망이다. 브랜드가 크리에이터를 단순 채널이 아닌 전략 파트너로 투자하는 시대가 열렸다.",
  "B2the7", "https://www.b2the7.com/news-blog/marketing-trends-june-2026-ai-search-chatgpt-ads-meta"),
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
