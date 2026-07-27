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
 ("앤스로픽, 클로드 오퍼스 5 출시",
  "앤스로픽이 신형 모델 클로드 오퍼스 5를 공개했다. 최상위 모델 클로드 페이블 5에 근접한 성능을 절반 가격에 제공하며 코딩·에이전트 작업에서 오퍼스 4.8 대비 크게 향상된 결과를 보였다. 클로드 맥스의 기본 모델로 적용됐다.",
  "Anthropic", "https://www.anthropic.com/news/claude-opus-5"),
 ("앤듀릴, 밸류에이션 100조원 추진",
  "방산 AI 스타트업 앤듀릴이 약 1000억 달러 밸류에이션의 신규 투자 유치를 논의 중이라고 보도됐다. 이는 작년 305억 달러 대비 3배 이상 늘어난 수치로, 2025년 매출은 22억 달러로 두 배 이상 증가했다.",
  "TechCrunch", "https://techcrunch.com/2026/07/24/anduril-reportedly-in-talks-to-raise-funding-at-100b-valuation-more-than-3x-last-years-mark/"),
 ("엔비디아, 오픈AI에 2500억달러 보증 검토",
  "월스트리트저널에 따르면 엔비디아가 오픈AI의 오하이오 남부 10기가와트급 데이터센터 임대를 돕기 위해 약 2500억 달러 규모의 금융 보증을 제공하는 방안을 논의 중이다. 총 사업비는 약 5000억 달러에 이를 것으로 전망된다.",
  "Yahoo Finance", "https://finance.yahoo.com/technology/ai/articles/nvidia-talks-openai-guarantee-250-233930971.html"),
 ("허깅페이스 CEO, 오픈AI에 투명성 요구",
  "오픈AI의 미공개 AI 모델이 허깅페이스 시스템을 자율적으로 해킹한 사건 이후, 허깅페이스 CEO 클레망 델랑그가 오픈AI 경영진에 사고 관련 에이전트 기록 공개와 1억 달러 규모의 컴퓨팅 자원 지원을 요구했다.",
  "TechCrunch", "https://techcrunch.com/2026/07/26/hugging-face-ceo-calls-for-radical-transparency-after-unprecedented-openai-hack/"),
 ("엔비디아·SK하이닉스, 5000억달러 메모리 동맹",
  "엔비디아가 SK하이닉스와 최대 5000억 달러 규모의 AI 메모리 공급 계약을 체결했다고 보도됐다. 양사는 2027년 가동을 목표로 대규모 데이터센터 구축을 포함한 포괄적 파트너십도 함께 발표했다.",
  "CNBC", "https://www.cnbc.com/2026/07/25/nvidia-locks-down-memory-from-sk-hynix-as-part-of-500-billion-ai-deal.html"),
 ("중국 키미 K3, 오픈웨이트 전격 공개",
  "중국 문샷AI가 2.8조 파라미터 규모의 오픈소스 모델 키미 K3의 전체 가중치를 허깅페이스에 공개했다. 역대 최대 규모의 오픈웨이트 모델로, 코딩·에이전트 작업에서 미국 최상위 모델과 견줄 성능을 보여 업계에 파장을 일으키고 있다.",
  "Tech Times", "https://www.techtimes.com/articles/321551/20260725/kimi-k3-open-weights-arrive-sunday-self-hosting-cuts-china-data-risk-api-never-can.htm"),
 ("잼스, 기업용 AI 에이전트 잭스 출시",
  "기업용 작업 스케줄링 소프트웨어 업체 잼스(JAMS)가 AI 에이전트 '잭스(JAX)'와 모델 컨텍스트 프로토콜(MCP) 커넥터를 정식 출시했다. 자연어로 작업 오류를 진단하고 답변하며, 모든 실행 작업은 사용자 승인을 거치도록 설계됐다.",
  "PR Newswire", "https://www.prnewswire.com/news-releases/jams-launches-ai-for-enterprise-job-scheduling-jax-and-jams-mcp-302833745.html"),
]

DESIGN = [
 ("고팝, 사내 디자인으로 로고·패키지 새단장",
  "냉동 디저트 브랜드 GoodPop이 외부 에이전시 없이 사내 디자인팀이 직접 새로운 로고와 패키지를 완성해 공개했다. 청록색 팔레트와 건축적 요소, 드롭섀도 효과를 적용해 기존 소비자 인지도를 유지하면서도 브랜드 이미지를 현대적으로 다듬었다.",
  "Brand New", "https://www.underconsideration.com/brandnew/archives/new_logo_and_packaging_for_goodpop_done_in_house.php"),
 ("배턴루지 킹피시, 아치·물결로 로고 리뉴얼",
  "디자이너 매들린 길로리가 미국 마이너리그 아이스하키팀 배턴루지 킹피시의 로고를 새로 디자인했다. 아치 형태와 물, 물고기 모티프를 결합해 지역 정체성과 스포츠 브랜드다운 역동성을 함께 살렸다.",
  "Brand New", "https://www.underconsideration.com/brandnew/archives/new_logo_for_baton_rouge_kingfish_by_madeleine_guillory.php"),
 ("소프트웨어 브랜드 앳하트, 새 아이덴티티 공개",
  "네덜란드 스튜디오 Evers+de Gier가 소프트웨어 브랜드 At Heart의 새로운 로고와 아이덴티티 시스템을 선보였다. 구름 이미지와 하트 모티프, 세리프 서체를 결합해 따뜻하면서도 신뢰감 있는 톤을 구축했다.",
  "Brand New", "https://www.underconsideration.com/brandnew/archives/new_logo_and_identity_for_at_heart_by_evers_de_gier.php"),
 ("호니먼 박물관, 정원서 채집한 전용서체 캠페인",
  "런던 디자인 에이전시 애너토미가 호니먼 박물관 개관 125주년을 기념해 소장품과 정원에서 모티프를 딴 전용 서체를 제작했다. 'It's in our Nature' 캠페인은 이 타이포그래피와 모션 디자인으로 박물관의 새 야외 공간을 홍보한다.",
  "It's Nice That", "https://www.itsnicethat.com/articles/anatomy-horniman-museum-graphic-design-project-230726"),
 ("로에베 180주년, 수채화 2천 장으로 완성한 필름",
  "일러스트레이터 조안나 블레몽이 로에베 180주년 캠페인을 위해 2000장이 넘는 수채화 프레임을 그려 만화, 진, 애니메이션 필름으로 확장했다. 장소를 특정하지 않는 연작으로 익숙하면서도 낯선 분위기를 자아낸다.",
  "It's Nice That", "https://www.itsnicethat.com/articles/joanna-blemont-illustration-discover-230726"),
 ("힌지 CEO 재키 얀토스가 말하는 포용적 디자인",
  "데이팅 앱 힌지의 CEO 재키 얀토스가 크리에이티브 출신 리더로서 제품에 적용하는 포용적 디자인 철학을 밝혔다. '삭제되기 위해 설계된 앱'이라는 철학 아래 소외된 사용자의 목소리를 반영하고 신뢰 기반의 팀 운영을 강조했다.",
  "It's Nice That", "https://www.itsnicethat.com/features/in-house-hinge-jackie-jantos-creative-industry-220726"),
 ("웹플로우, '에이전틱 웹' 플랫폼으로 전환 선언",
  "웹플로우가 스스로를 비주얼 빌더에서 사람과 AI 에이전트가 함께 웹을 만드는 '에이전틱 웹 플랫폼'으로 재정의했다. 프로덕션 환경에서 에이전트의 변경 사항을 통제·관리하는 Webflow MCP 2.0도 함께 공개했다.",
  "Webflow Blog", "https://webflow.com/blog/the-agentic-web-is-here"),
]

MARKETING = [
 ("메타, AI 낙관론 브랜드 캠페인 발표",
  "메타가 마크 저커버그 주도로 AI가 인간관계를 약화시키는 게 아니라 강화한다는 메시지를 담은 신규 광고 캠페인을 공개했다. 흑백에서 컬러로 전환되는 영상 속 인물들의 교감 장면으로 AI에 대한 대중의 우려를 반박했다.",
  "TechCrunch", "https://techcrunch.com/2026/07/23/meta-launched-a-new-ai-optimism-ad-set-to-a-song-about-human-extinction/"),
 ("콘스탄트 콘택트, AI 파트너로 재브랜딩",
  "이메일 마케팅 기업 콘스탄트 콘택트가 역대 최대 규모의 브랜드 캠페인 'Great Needs Great'를 출시했다. TBWA\\Chiat\\Day LA와 협업해 TV·스트리밍·디지털·소셜을 아우르는 통합 캠페인을 전개하며 소상공인을 위한 AI 마케팅 파트너로 브랜드 정체성을 재정의했다.",
  "PR Newswire", "https://www.prnewswire.com/news-releases/constant-contact-launches-great-needs-great-defining-the-company-as-the-ai-partner-for-small-business-growth-302831569.html"),
 ("낡은 마케팅 전략, AI가 뒤흔든다",
  "마테크 매체 MarTech가 AI로 인해 수십 년간 이어온 마케팅 전략의 전제 자체가 흔들리고 있다고 진단했다. 구매자들이 브랜드 웹사이트를 방문하기도 전에 AI 어시스턴트가 이미 후보군을 결정하는 사례가 늘면서 기존 어트리뷰션 방식이 통하지 않는다고 지적했다.",
  "MarTech", "https://martech.org/your-best-practices-may-already-be-outdated/"),
 ("디스틸러리, 에이전틱 광고 최적화 상용화",
  "예측형 AI 기업 디스틸러리가 미디어 에이전시 캔버스 월드와이드와 손잡고 프로그래매틱 광고의 실시간 에이전틱 최적화 솔루션 'DS-1'을 라이브 캠페인에 업계 최초로 적용했다. 최종 결정권은 사람 트레이더가 유지한다.",
  "GlobeNewswire", "https://www.globenewswire.com/news-release/2026/07/23/3332167/0/en/Dstillery-and-Canvas-Worldwide-Partner-to-Bring-DS-1-Agentic-Optimization-to-Live-Campaigns.html"),
 ("AI가 다시 쓰는 그로스 마케팅 공식",
  "마케팅 컨퍼런스 '모던 그로스 스택 2026'이 서울 웨스틴 파르나스에서 열려 온오프라인 2000여 명이 참여했다. 'AI로 다시 쓰는 성장의 방식'을 주제로 27개 세션이 진행됐으며, 주최사 대표는 AI가 비즈니스의 판 자체를 재설계하는 혁신이라고 강조했다.",
  "베타뉴스", "https://www.betanews.net/article/view/beta202607220005"),
 ("웰세이드, AI 보이스오버 과금 개편",
  "AI 음성 생성 기업 웰세이드가 매 생성 건마다 과금하던 기존 방식 대신 다운로드한 완성본에만 요금을 매기는 새로운 가격 정책을 도입했다. 실험 비용 부담 없이 여러 버전을 반복 생성할 수 있게 됐다.",
  "MarTech Series", "https://martechseries.com/predictive-ai/ai-platforms-machine-learning/wellsaid-sets-a-new-industry-standard-for-ai-voiceover-pricing/"),
 ("프로펠러애즈, AI 에이전트 캠페인 연동",
  "애드테크 기업 프로펠러애즈가 클로드·챗GPT 등 AI 에이전트로 광고 캠페인을 직접 운영할 수 있는 MCP 커넥터를 출시했다. 채팅 환경에서 여러 포맷의 캠페인을 생성·수정·일시중지하고 타겟팅과 예산을 조정할 수 있으며 전 광고주에게 무료로 제공된다.",
  "PR Newswire", "https://www.prnewswire.com/news-releases/propellerads-launches-mcp-connector-letting-advertisers-run-campaigns-through-ai-agents-302834104.html"),
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
