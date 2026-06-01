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
import os, re, io, html, datetime, hashlib, math
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

# 동일 이미지가 두 카드에 들어가지 않도록 16x16 그레이스케일 지문으로 중복 검출
_SEEN_HASHES = set()
def _img_hash(img):
    return hashlib.md5(img.convert("L").resize((16, 16)).tobytes()).hexdigest()

def placeholder(accent, label, bw, bh, seed=0):
    """카드마다 시각적으로 구별되는 액센트 플레이스홀더 (그라데이션 각도·패턴·번호가 seed별로 달라짐)."""
    base = Image.new("RGB", (bw, bh), accent)
    top  = Image.new("RGB", (bw, bh), tuple(min(255, c+60) for c in accent))
    mask = Image.linear_gradient("L").rotate((seed*53 + 20) % 360, expand=False, resample=Image.BICUBIC)
    mask = mask.resize((bw, bh))
    img = Image.composite(base, top, mask)
    d = ImageDraw.Draw(img, "RGBA")
    # seed별로 위치·크기가 달라지는 반투명 도형으로 패턴 차별화
    r = 180 + (seed % 4) * 60
    cx = int(bw * (0.2 + 0.15 * (seed % 5)))
    cy = int(bh * (0.3 + 0.12 * (seed % 3)))
    d.ellipse([cx-r, cy-r, cx+r, cy+r], fill=(255, 255, 255, 28))
    r2 = 90 + (seed % 3) * 40
    cx2 = int(bw * (0.7 - 0.1 * (seed % 4)))
    cy2 = int(bh * (0.65 + 0.08 * (seed % 4)))
    d.ellipse([cx2-r2, cy2-r2, cx2+r2, cy2+r2], fill=(0, 0, 0, 22))
    # 출처명 (중앙)
    f = font("extrabold", 54)
    tw = d.textlength(label, font=f)
    d.text(((bw-tw)//2, bh//2-36), label, font=f, fill=(255, 255, 255))
    f2 = font("semibold", 28)
    sub = "FAFA NEWS"
    d.text(((bw-d.textlength(sub, font=f2))//2, bh//2+44), sub, font=f2, fill=(255, 255, 255, 220))
    return img

def get_card_image(url, accent, source, bw, bh, key, seed=0):
    cache = os.path.join(IMG_CACHE, key+".png")
    if os.path.exists(cache):
        img = Image.open(cache).convert("RGB")
        _SEEN_HASHES.add(_img_hash(img))
        return img
    raw = fetch_og_image(url)
    if raw:
        img = cover_crop(raw, bw, bh)
        h = _img_hash(img)
        if h in _SEEN_HASHES:          # 이미 쓴 이미지면 카드별 플레이스홀더로 대체
            img = placeholder(accent, source, bw, bh, seed)
    else:
        img = placeholder(accent, source, bw, bh, seed)
    _SEEN_HASHES.add(_img_hash(img))
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
    img = get_card_image(url, ac, source, W, BH, fn.split(".")[0], seed=idx)
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
  "OpenAI가 역대 최강 모델 GPT-5.5를 공개했다. 에이전틱 코딩·컴퓨터 사용·지식 작업에서 큰 폭의 성능 향상을 보이며, 복잡한 멀티스텝 업무 자동화를 겨냥한다.",
  "OpenAI", "https://openai.com/index/introducing-gpt-5-5/"),
 ("Anthropic, Claude Opus 4.8 출시",
  "Anthropic이 플래그십 Claude Opus 4.8을 내놨다. 추론 강도(effort)를 직접 조절하고 병렬 서브에이전트를 돌리는 동적 워크플로를 지원해 에이전틱 작업의 통제권을 사용자에게 넘겼다.",
  "Anthropic", "https://www.anthropic.com/news"),
 ("DeepSeek, V4-Pro·Flash 동시 공개",
  "DeepSeek이 100만 토큰 컨텍스트의 MoE 모델 V4-Pro·Flash를 출시했다. 하이브리드 어텐션으로 추론 비용을 낮춰, 장시간 도는 에이전트 작업의 경제성을 끌어올린 점이 핵심이다.",
  "LLM Stats", "https://llm-stats.com/llm-updates"),
 ("GitHub Copilot, '사용량 기반' 과금으로 전환",
  "GitHub이 6월 1일부로 Copilot을 요청 기반에서 'AI 크레딧' 사용량 과금으로 바꿨다. 에이전트가 토큰을 많이 쓰는 시대에 맞춰 비용 구조를 재설계한 신호다.",
  "GitHub Blog", "https://github.blog/"),
 ("OpenAI, 9월 IPO 정조준 — 기밀 서류 제출",
  "OpenAI가 골드만삭스·모건스탠리를 주관사로 IPO 기밀 서류 작업에 들어갔다. 9월 상장이 거론되며 밸류에이션은 약 8,520억 달러 수준으로 추산된다.",
  "Crescendo AI", "https://www.crescendo.ai/news/latest-ai-news-and-updates"),
 ("OpenRouter, 시리즈B 1.13억 달러 — 기업가치 13억",
  "AI 모델 통합 라우팅 플랫폼 OpenRouter가 1억 1,300만 달러를 조달해 13억 달러 밸류에이션에 올랐다. 주당 25조 토큰을 처리하며 '모델 중립' 인프라 수요를 입증했다.",
  "Qubit Capital", "https://qubit.capital/blog/ai-startup-fundraising-trends"),
 ("MIT: 기업 85% '에이전틱 전환' 원하지만 76%는 준비 안 됨",
  "MIT Technology Review 조사에서 기업 85%가 3년 내 에이전틱 전환을 목표로 했지만 76%는 인프라가 부족했다. AI 도입의 진짜 병목이 모델이 아닌 조직 역량임을 드러낸다.",
  "MIT Tech Review", "https://www.technologyreview.com/"),
]

DESIGN = [
 ("Figma Config 2026, 6월 23-25일 샌프란시스코 개막",
  "제품 크리에이터 연례 컨퍼런스 Config 2026이 열린다. 실제 컴포넌트 라이브러리를 기반으로 한 AI UI 생성과 프로토타입 자동화가 올해의 화두로 예고됐다.",
  "Figma", "https://config.figma.com/"),
 ("UX London 2026, 6월 2일부터 사흘간",
  "유럽 최대 UX 컨퍼런스가 막을 올린다. AI 에이전트 시대의 인터페이스 설계와 인간 중심 UX의 재정의가 핵심 세션으로 다뤄진다.",
  "UIUX Trend", "https://uiuxtrend.com/events/"),
 ("디자인이 '실행 가능한 인프라'가 된다",
  "브랜드 규칙을 PDF가 아닌 머신리더블 마크다운으로 옮기는 흐름이 빨라졌다. AI가 생성 단계에서 브랜드 기준을 강제하면서 비전문가도 일관된 산출물을 뽑아낸다.",
  "Mean.ceo", "https://blog.mean.ceo/design-trends-june-2026/"),
 ("Motion-First — 정적 로고의 시대가 저문다",
  "키네틱·적응형 시스템으로 옮겨가는 Motion-First가 2026 브랜딩의 표준이 됐다. 화면·맥락에 따라 변하는 다이나믹 아이덴티티가 디지털·몰입형 환경 전반에 적용된다.",
  "Big Orange Planet", "https://www.bigorangeplanet.com/2026/05/20/top-10-branding-trends-in-2026"),
 ("제품 디자인, 'AI 동일성'을 버리고 의도로 회귀",
  "AI가 찍어낸 듯한 비슷한 디자인에서 벗어나, 의도와 맥락을 앞세운 제품 디자인이 부상한다. 기계를 위한 설계(MX)와 인간적 결정 사이의 균형이 핵심 과제로 떠올랐다.",
  "UX Pilot", "https://uxpilot.ai/blogs/product-design-trends"),
 ("접근성이 '기능'에서 '인프라'로 격상",
  "2026 UX 설계는 접근성을 선택이 아닌 기반 시설로 다룬다. 시각·운동·인지·기기·환경의 다양성을 기본값으로 전제하는 설계가 업계 표준으로 자리잡았다.",
  "UXPin", "https://www.uxpin.com/studio/blog/ui-ux-design-trends/"),
 ("Raw & Imperfect — AI 매끈함에 대한 반작용",
  "거친 타이포·불균형 레이아웃·스캔 텍스처 등 '의도된 불완전함'이 새 크리에이티브 기준으로 떠올랐다. 천편일률적 미니멀리즘에 대한 피로가 만든 흐름이다.",
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
