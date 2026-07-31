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
import os, re, io, html, datetime, hashlib
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

def ensure_fonts():
    """Pretendard(한글) 폰트가 없으면 npm으로 자동 설치. 그래도 없으면 에러로 중단
    — 한글이 □□□(두부)로 깨진 PDF가 만들어지는 사고를 원천 차단한다."""
    import subprocess, glob
    need = os.path.join(FONT_DIR, F["black"])
    if os.path.exists(need):
        return
    os.makedirs(FONT_DIR, exist_ok=True)
    try:
        subprocess.run("npm install pretendard@1.3.9", cwd=FONT_DIR, shell=True,
                       check=False, capture_output=True, timeout=180)
        for f in glob.glob(os.path.join(FONT_DIR, "node_modules/pretendard/dist/public/static/*.otf")):
            dst = os.path.join(FONT_DIR, os.path.basename(f))
            if not os.path.exists(dst):
                import shutil; shutil.copy(f, dst)
    except Exception as e:
        print("폰트 설치 시도 실패:", e)
    if not os.path.exists(need):
        raise RuntimeError(
            f"Pretendard 폰트를 찾을 수 없습니다 ({need}). 한글이 깨지므로 렌더링을 중단합니다. "
            "`cd /tmp/fonts && npm install pretendard@1.3.9 && "
            "cp node_modules/pretendard/dist/public/static/*.otf /tmp/fonts/` 실행 후 재시도하세요.")

ensure_fonts()
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
def _abs_url(src, base_url):
    src = html.unescape(src).strip()
    if src.startswith("//"): return "https:" + src
    if src.startswith("/"):
        from urllib.parse import urlparse
        p = urlparse(base_url); return f"{p.scheme}://{p.netloc}" + src
    return src

def _load_image(src):
    try:
        ir = requests.get(src, headers=UA, timeout=12)
        ct = ir.headers.get("Content-Type", "")
        if ir.status_code != 200 or len(ir.content) < 3000: return None
        if "svg" in ct or src.lower().endswith(".svg"): return None
        img = Image.open(io.BytesIO(ir.content)).convert("RGB")
        if img.width < 200 or img.height < 150: return None  # 로고·아이콘 배제
        return img
    except Exception:
        return None

def fetch_og_image(url):
    """기사 페이지에서 대표 이미지 추출 — 여러 메타/JSON-LD/본문 이미지를 순차 시도."""
    try:
        r = requests.get(url, headers=UA, timeout=12)
        if r.status_code != 200: return None
        h = r.text
    except Exception:
        return None
    cands = []
    for pat in (
        r'<meta[^>]+property=["\']og:image(?::secure_url)?["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
        r'<meta[^>]+name=["\']twitter:image(?::src)?["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image["\']',
        r'<link[^>]+rel=["\']image_src["\'][^>]+href=["\']([^"\']+)',
        r'<meta[^>]+itemprop=["\']image["\'][^>]+content=["\']([^"\']+)',
    ):
        cands += re.findall(pat, h, re.I)
    # JSON-LD "image": "..." 또는 "image": ["..."]
    for m in re.findall(r'"image"\s*:\s*(\[[^\]]+\]|"[^"]+")', h, re.I):
        cands += re.findall(r'https?:[^"\']+', m)
    # 본문 내 큰 이미지(article 우선)
    body = re.search(r'<article[\s\S]{0,40000}?</article>', h, re.I)
    scope = body.group(0) if body else h
    cands += re.findall(r'<img[^>]+(?:data-src|src)=["\'](https?://[^"\']+\.(?:jpg|jpeg|png|webp)[^"\']*)', scope, re.I)
    seen = set()
    for c in cands:
        src = _abs_url(c, url)
        if src in seen: continue
        seen.add(src)
        img = _load_image(src)
        if img: return img
    return None

# 한국어 브랜드·인물명 → 영어 변환 (Openverse 검색 품질 향상)
KO_TO_EN = {
    "안드레이 카파시": "Andrej Karpathy",
    "카파시": "Karpathy",
    "구글": "Google", "알파벳": "Alphabet Google",
    "엔비디아": "NVIDIA", "엔비디아의": "NVIDIA",
    "마이크로소프트": "Microsoft", "MS": "Microsoft",
    "깃허브": "GitHub", "코파일럿": "GitHub Copilot",
    "오픈AI": "OpenAI", "ChatGPT": "ChatGPT",
    "앤트로픽": "Anthropic", "Anthropic": "Anthropic",
    "피그마": "Figma", "Figma": "Figma",
    "애플": "Apple", "메타": "Meta",
    "아마존": "Amazon", "AWS": "AWS",
    "스냅": "Snapchat Snap",
    "타입폼": "Typeform",
    "펍매틱": "PubMatic programmatic",
    "세일즈포스": "Salesforce",
    "틱톡": "TikTok",
    "유튜브": "YouTube",
    "링크드인": "LinkedIn",
    "트위터": "Twitter X",
    "펩시코": "PepsiCo",
    "루프트한자": "Lufthansa",
    "BMW": "BMW",
    "BBH": "BBH advertising agency",
    "리브랜드": "rebrand identity",
    "브랜딩": "branding",
    "광고": "advertising",
    "캠페인": "marketing campaign",
    "에이전트": "AI agent",
    "펀딩": "startup funding",
    "밸류에이션": "startup valuation",
    "거버넌스": "AI governance policy",
    "크리에이터": "creator content",
}

def _img_queries(title, cat_en):
    """제목에서 구체적 검색어 추출 → 카테고리 폴백 순서로 반환.
    한국어 브랜드·인물명을 영어로 변환해 Openverse 검색 정밀도를 높인다."""
    # 한국어 → 영어 치환
    t = title
    for ko, en in KO_TO_EN.items():
        t = t.replace(ko, en)
    # 영어 단어 추출 (3글자 이상, 숫자·기호 제외)
    en_words = re.findall(r"[A-Z][A-Za-z]{2,}|[A-Za-z]{4,}", t)
    # 브랜드명·고유명사(대문자 시작) 우선
    proper = [w for w in en_words if w[0].isupper()]
    common = [w for w in en_words if not w[0].isupper()]
    fallback = {"AI":         ["artificial intelligence", "AI technology", "machine learning"],
                "DESIGN":     ["graphic design", "brand identity", "design studio"],
                "MARKETING":  ["marketing advertising", "brand campaign", "digital marketing"],
                }.get(cat_en, ["technology"])
    qs = []
    # 가장 구체적: 고유명사 2개 조합
    if len(proper) >= 2: qs.append(f"{proper[0]} {proper[1]}")
    if proper:           qs.append(proper[0])
    # 고유명사 + 카테고리 힌트
    if proper:           qs.append(f"{proper[0]} {fallback[0]}")
    # 일반 단어 + 힌트
    if common:           qs.append(f"{common[0]} {fallback[0]}")
    qs += fallback
    seen = set()
    return [q for q in qs if not (q in seen or seen.add(q))]

def fetch_related_image(queries, salt=0):
    """og:image 실패 시 Openverse(무료 이미지 검색)로 주제 관련 실사진을 가져온다.
    salt로 결과 순서를 회전시켜 카드마다 다른 이미지를 고른다."""
    if isinstance(queries, str): queries = [queries]
    for q in queries:
        try:
            r = requests.get("https://api.openverse.org/v1/images/",
                             params={"q": q, "page_size": 12, "mature": "false"},
                             headers=UA, timeout=12)
            if r.status_code != 200: continue
            results = r.json().get("results", [])
        except Exception:
            continue
        if not results: continue
        n = len(results); off = salt % n
        for i in [(off + j) % n for j in range(n)]:
            res = results[i]
            for u in (res.get("url"), res.get("thumbnail")):
                if not u: continue
                img = _load_image(u)
                if img: return img
    return None

def cover_crop(img, bw, bh):
    iw, ih = img.size
    scale = max(bw/iw, bh/ih)
    nw, nh = int(iw*scale)+1, int(ih*scale)+1
    img = img.resize((nw, nh), Image.LANCZOS)
    x = (nw-bw)//2; y = (nh-bh)//2
    return img.crop((x, y, x+bw, y+bh))

def placeholder(accent, label, bw, bh, seed=""):
    # seed로 그라데이션 방향·색을 다르게 해 카드마다 고유한 플레이스홀더 생성
    s = int(hashlib.md5((seed or label).encode()).hexdigest(), 16)
    shift = 28 + (s % 46)
    c2 = tuple(min(255, c + shift) for c in accent)
    base = Image.new("RGB", (bw, bh), accent)
    top  = Image.new("RGB", (bw, bh), c2)
    mask = Image.linear_gradient("L").resize((bw, bh))
    if s & 1:   mask = mask.transpose(Image.FLIP_TOP_BOTTOM)
    if s & 2:   mask = mask.rotate(90, expand=True).resize((bw, bh))
    img = Image.composite(base, top, mask)
    d = ImageDraw.Draw(img)
    f = font("extrabold", 56)
    tw = d.textlength(label, font=f)
    d.text(((bw-tw)//2, bh//2-40), label, font=f, fill=(255,255,255))
    f2 = font("medium", 30)
    sub = "FAFA NEWS"
    d.text(((bw-d.textlength(sub, font=f2))//2, bh//2+40), sub, font=f2, fill=(255,255,255))
    return img

# 같은 사진이 두 번 들어가지 않도록 사용한 이미지 해시를 추적
_SEEN_HASHES = set()

def _ihash(img):
    return hashlib.md5(img.resize((64, 64)).tobytes()).hexdigest()

def get_card_image(url, accent, source, bw, bh, key, title="", cat_en="", force_search=False):
    """force_search=True: og:image를 건너뛰고 바로 제목 기반 이미지 검색(동일 URL 중복 카드용)."""
    cache = os.path.join(IMG_CACHE, key + ".png")
    salt  = int(hashlib.md5(key.encode()).hexdigest(), 16)
    qs    = _img_queries(title, cat_en)
    if os.path.exists(cache):
        img = Image.open(cache).convert("RGB")
    else:
        if force_search:
            fetched = fetch_related_image(qs, salt)
        else:
            fetched = fetch_og_image(url) or fetch_related_image(qs, salt)
        img = cover_crop(fetched, bw, bh) if fetched else placeholder(accent, source, bw, bh, key)
        img.save(cache)
    # 중복(동일 사진) 감지 → 다른 salt로 재검색, 그래도 겹치면 플레이스홀더
    if _ihash(img) in _SEEN_HASHES:
        salt2 = int(hashlib.md5((key + "alt").encode()).hexdigest(), 16)
        alt   = fetch_related_image(qs, salt2)
        img   = cover_crop(alt, bw, bh) if (alt and _ihash(cover_crop(alt, bw, bh)) not in _SEEN_HASHES) \
                else placeholder(accent, source, bw, bh, key)
        img.save(cache)
    _SEEN_HASHES.add(_ihash(img))
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

def card(idx, total, cat_en, cat_ko, ac, title, body, source, url, fn, force_search=False):
    im, d = base(CREAM)
    BH = 620
    img = get_card_image(url, ac, source, W, BH, fn.split(".")[0], title, cat_en, force_search)
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
# 날짜는 한국 시간(KST) 기준 '오늘'로 자동 설정 — 스케줄 실행 시 날짜가 어긋나지 않게 한다.
# (특정 날짜로 고정하려면 아래 두 줄을 직접 값으로 바꾼다.)
_KST    = datetime.timezone(datetime.timedelta(hours=9))
_TODAY  = datetime.datetime.now(_KST).date()
_WD     = ["월", "화", "수", "목", "금", "토", "일"]
DATE_ISO = _TODAY
DATE = f"{_TODAY.year}년 {_TODAY.month}월 {_TODAY.day}일 ({_WD[_TODAY.weekday()]})"

AI = [
 ("EU, 100억 유로 규모 AI 기가팩토리 7곳 조성 착수",
  "유럽연합이 7개 AI 기가팩토리 구축에 공공자금 100억 유로를 투입하는 공모를 7월 30일 개시했다. 민간 투자를 포함하면 총 300억 유로 규모로, 입찰은 11월 12일 마감되고 2027년 초 낙찰자가 발표된다.",
  "Yahoo Finance", "https://finance.yahoo.com/technology/ai/articles/eu-aims-seven-ai-gigafactories-112825098.html"),
 ("엔스케일, 16.5억 달러에 Ray 개발사 애니스케일 인수",
  "AI 클라우드 기업 엔스케일이 오픈소스 Ray 프레임워크 개발사 애니스케일을 약 16억 5천만 달러에 인수하는 계약을 7월 30일 체결했다. 전력·데이터센터·GPU부터 소프트웨어까지 AI 스택 전 계층을 직접 소유하겠다는 전략이다.",
  "TechCrunch", "https://techcrunch.com/2026/07/30/nscale-buys-anyscale-as-it-seeks-to-own-more-of-the-ai-compute-stack/"),
 ("메타, 2분기 매출 28% 급증에도 AI 투자로 잉여현금흐름 급감",
  "메타의 2026년 2분기 매출이 전년 대비 28% 증가한 608억 달러를 기록했지만, AI 인프라 투자로 연간 자본지출 전망을 최대 1450억 달러로 상향했다. 잉여현금흐름은 91% 급감했다.",
  "Variety", "https://variety.com/2026/digital/news/meta-q2-2026-earnings-results-legal-proceedings-charge-1236823577/"),
 ("AI 업계 종사자 1100여 명, \"발전 속도 조절 메커니즘\" 촉구",
  "오픈AI·Anthropic·구글·메타 소속 직원 1100명 이상이 7월 28일 미국 정부에 AI 개발 속도를 검증 가능하게 조율할 국제적 페이싱 메커니즘 마련을 촉구하는 공개서한을 발표했다. 두 회사는 발표 몇 시간 만에 회사 차원에서 지지 의사를 밝혔다.",
  "CNN", "https://www.cnn.com/2026/07/28/tech/ai-development-tech-employees-open-letter"),
 ("코그니잔트-Anthropic, 클로드 파트너십 최상위 등급으로 확대",
  "코그니잔트가 7월 27일 Anthropic의 최상위 파트너인 '글로벌 프리미어 파트너'로 올라서며 클로드를 산업별 플랫폼에 내장하기로 했다. 코그니잔트 직원 3만 명 이상이 이미 클로드 교육을 이수했다.",
  "Anthropic", "https://www.anthropic.com/news/cognizant-anthropic"),
 ("구글, Gemini Notebook에 대화형 앱 생성 기능 준비 중",
  "구글이 Gemini Notebook(옛 NotebookLM) 스튜디오 패널에 소스 자료를 바탕으로 대시보드나 학습 도구 같은 인터랙티브 앱을 만들어주는 'Apps' 기능을 개발 중인 정황이 7월 29일 포착됐다. 아직 정식 출시 일정은 없다.",
  "TestingCatalog", "https://www.testingcatalog.com/google-is-working-on-interactive-apps-for-gemini-notebook/"),
 ("엔비디아 파트너 칩에이전츠, 6천만 달러 추가 유치",
  "AI 에이전트로 반도체 설계를 가속하는 칩에이전츠가 7월 29일 6천만 달러 추가 투자를 유치해 시리즈A 누적 조달액이 1억 3100만 달러로 늘었다. 엔비디아와의 협력도 전용 칩 설계 모델 개발로 확대됐다.",
  "Yahoo Finance", "https://finance.yahoo.com/technology/ai/articles/nvidia-partner-chipagents-raises-60-114953354.html"),
]

DESIGN = [
 ("100년 만에 존재감을 찾은 거너스버리 박물관",
  "런던 거너스버리 박물관이 비덴만 램프와 함께 100년 만에 처음으로 브랜드 아이덴티티를 새로 구축했다. 공원 속에 묻혀 있던 존재감을 살려 방문객이 '발견'할 이유를 만드는 데 초점을 맞췄다.",
  "Creative Boom", "https://www.creativeboom.com/news/for-100-years-nobody-noticed-the-museum-in-the-park-wiedemann-lampes-rebrand-finally-gives-gunnersbury-a-reason-to-be-found/"),
 ("\"펀드매니저가 뭐하는 사람이죠?\" 대형 설치미술로 답하다",
  "자산운용사 T. 로우 프라이스가 사람들이 잘 모르는 펀드매니저의 역할을 설명하기 위해 대형 아트 설치물을 제작했다. 딱딱한 금융 브랜드를 체험형 비주얼 캠페인으로 풀어낸 사례다.",
  "Creative Boom", "https://www.creativeboom.com/news/most-people-dont-know-what-a-fund-manager-actually-does-so-t-rowe-price-built-a-giant-art-installation-to-explain-it/"),
 ("차차안텡×하인즈, 마요네즈 자판기로 만든 기발한 캠페인",
  "크리에이티브 스튜디오 차차안텡이 하인즈를 위해 만든 '오토 마요-마트' 설치가 화제다. AI 생성 이미지가 범람하는 시대에 손으로 만든 물리적 장치로 존재감을 드러낸 그래픽 프로젝트다.",
  "It's Nice That", "https://www.itsnicethat.com/articles/chachaanteng-heinz-mayo-graphic-design-project-300726"),
 ("오버뷰, 다큐멘터리 아이덴티티에 '표현'을 담다",
  "오버뷰가 다큐멘터리 잔드란드를 위해 만든 아이덴티티는 구두점과 글리프를 활용해 가독성보다 표현력을 앞세운 실험적 그래픽 작업이다.",
  "It's Nice That", "https://www.itsnicethat.com/articles/overview-zandland-human-graphic-design-project-300726"),
 ("억셉트&프로시드, 반스 브랜드에 '명료함'을 처방하다",
  "디자인 스튜디오 억셉트&프로시드가 스니커즈 브랜드 반스를 위해 만든 새 시스템은 복잡해진 아이코닉 브랜드에 명확한 위계를 되찾아준 사례로 꼽힌다.",
  "It's Nice That", "https://www.itsnicethat.com/articles/accept-and-proceed-vans-graphic-design-290726"),
 ("버프모션, 스탈링 모션 아이덴티티의 핵심은 '자신감'",
  "모션 스튜디오 버프모션이 만든 핀테크 브랜드 스탈링의 모션 아이덴티티는 돈이 아니라 자신감이라는 감정을 중심으로 설계됐다.",
  "Creative Boom", "https://www.creativeboom.com/news/buff-motions-motion-identity-for-starling-is-built-around-confidence-not-cash/"),
 ("노마드, 그리스 최대 베팅 브랜드 스토이히만을 다시 그리다",
  "디자인 에이전시 노마드가 그리스 토종 브랜드에서 국가 대표 베팅 브랜드로 성장한 스토이히만의 아이덴티티를 새로 개편했다.",
  "Creative Boom", "https://www.creativeboom.com/news/how-nomad-reimagined-stoiximan-the-homegrown-brand-that-became-greeces-biggest-name-in-betting/"),
]

MARKETING = [
 ("구글, 제미나이 기반 '대화형 디스커버리 광고' 미국 테스트",
  "구글 마케팅 라이브에서 공개된 제미나이 기반 광고 포맷이 미국 내 AI 모드·검색에서 테스트에 들어갔다. 사용자의 질문 맥락에 맞춰 실시간으로 광고 크리에이티브를 생성하는 방식이다.",
  "Search Engine Land", "https://searchengineland.com/google-tests-new-conversational-ad-formats-in-ai-mode-and-search-478115"),
 ("스택어댑트, AI 퍼스트 광고 허브 'Ivy 스튜디오' 출시",
  "스택어댑트가 7월 28일 자연어로 오디언스를 탐색하고 캠페인을 예측·실행하는 AI 허브 Ivy 스튜디오를 공개했다. 마케터가 대시보드 대신 대화로 광고 운영 전반을 다루도록 돕는다.",
  "ExchangeWire", "https://www.exchangewire.com/blog/2026/07/28/stackadapt-introduces-ivy-studiotm-a-new-hub-for-ai-first-advertising/"),
 ("알골리아, AI 쇼핑 어시스턴트용 '에이전트 스튜디오' 확장",
  "알골리아가 7월 28일 재고·가격·개인화를 결합해 대화형 쇼핑 경험을 만드는 에이전트 스튜디오에 거버넌스·비용 통제 기능을 추가했다. 소매업체가 브랜드 기준을 지키며 AI 쇼핑 에이전트를 운영할 수 있게 됐다.",
  "Algolia", "https://www.algolia.com/about/news/algolia-launches-agent-studio-to-power-scalable-context-aware-ai-agents"),
 ("코인베이스 \"광고보다 중요한 건 마케팅 전략 자체\"",
  "코인베이스가 브랜드를 단순 커뮤니케이션이 아니라 제품·정책 전반을 조율하는 의사결정 도구로 격상시키고 있다. AI 없이 실사로 찍은 'Your Way Out' 캠페인도 '진짜'를 강조하는 전략의 일환이다.",
  "The Drum", "https://www.thedrum.com/news/for-coinbase-great-ads-are-the-least-important-part-of-the-marketing-plan"),
 ("오픈AI, 챗GPT 광고 관리자 셀프서브 전면 개방",
  "오픈AI가 5만 달러였던 최소 집행 기준을 없애고 ads.openai.com에서 누구나 챗GPT 광고를 집행할 수 있도록 열었다. 영국·멕시코·브라질·일본·한국으로 파일럿도 확대됐다.",
  "Digiday", "https://digiday.com/marketing/openai-opens-up-chatgpt-ads-manager-to-the-u-s-while-promising-third-party-measurement-cpa-bidding/"),
 ("인스타그램 릴스 '포스트뷰 광고' 전 광고주로 확대",
  "인스타그램이 릴스 시청 종료 후 노출되는 포스트뷰 광고를 전 세계 모든 광고주에게 개방했다. 60초 이상 유기적 릴스 뒤에 붙는 방식으로 캠페인 매니저에서 바로 설정할 수 있다.",
  "Social Media Today", "https://www.socialmediatoday.com/news/instagram-expands-reels-post-view-ads-to-all-advertisers/822317/"),
 ("링크드인, 캠페인 매니저에 AI 크리에이티브 도구 5종 탑재",
  "링크드인이 브랜드 키트·AI 초안 작성·광고 변형 자동 생성 등 5가지 AI 도구를 캠페인 매니저에 추가했다. 5개 이상 변형 광고를 운영한 캠페인은 클릭률이 20% 이상 높았다고 밝혔다.",
  "Social Samosa", "https://www.socialsamosa.com/news-2/linkedin-ai-tools-ad-creation-personalisation-12129858"),
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
    # 동일 URL을 2번 이상 쓰는 카드는 og:image가 무관한 이미지일 가능성이 높음
    # → 첫 번째 카드만 og:image, 나머지는 제목 기반 검색 강제
    all_urls = [u for _, _, _, _, items in SECTIONS for _, _, _, u in items]
    _seen_urls: set = set()
    def _force(url):
        if url in _seen_urls: return True
        _seen_urls.add(url); return False
    pages.append(cover(DATE, n_articles))
    idx = 2
    for cat_en, cat_ko, ac, suffix, items in SECTIONS:
        for t, b, s, u in items:
            pages.append(card(idx, total, cat_en, cat_ko, ac, t, b, s, u,
                              f"{idx:02d}_{suffix}.png", force_search=_force(u)))
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
