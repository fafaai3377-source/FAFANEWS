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
 ("클라우드플레어, AI 에이전트 전용 브라우저 '카이트서프' 출시",
  "클라우드플레어가 AI 에이전트를 위한 클라우드 호스팅 브라우저 카이트서프를 공개했다. 스크린샷 촬영이나 HTML 추출 등 에이전트 작업에서 크로미움보다 CPU·메모리 사용량이 훨씬 적은 것이 특징이다.",
  "TechCrunch", "https://techcrunch.com/2026/08/07/cloudflare-launches-kitesurf-a-browser-built-for-ai-agents/"),
 ("오픈AI, 차세대 모델 '아스트라' 사이버 위험으로 개발 일부 중단",
  "오픈AI는 차세대 모델 아스트라가 자체 대비 프레임워크상 '치명적' 사이버보안 위협 수준에 도달했을 가능성을 배제할 수 없다며, 강화된 보안 조치를 충족하지 못한 내부 작업을 일시 중단했다고 밝혔다.",
  "TechCrunch", "https://techcrunch.com/2026/08/07/openai-says-it-slowed-astra-model-development-over-security-concerns/"),
 ("오픈AI, 프레젠테이션 스타트업 넥스트슬라이드 인수",
  "오픈AI가 프레젠테이션 제작 스타트업 넥스트슬라이드를 인수했다고 발표했으며, 해당 팀은 챗GPT 개발 조직에 합류한다.",
  "TechCrunch", "https://techcrunch.com/2026/08/08/openai-acquires-presentation-startup-nextslide/"),
 ("엔비디아, AI 데이터센터 전력업체 랜시움에 최대 30억 달러 투자",
  "엔비디아가 스타게이트 프로젝트의 전력 인프라를 담당하는 랜시움에 최대 30억 달러를 투자하기로 했다고 알려졌다. 우선 20억 달러로 지분 약 20%를 확보하고, 목표 달성 시 10억 달러를 추가 투자할 수 있다.",
  "Investing.com", "https://www.investing.com/news/stock-market-news/nvidia-to-invest-up-to-3-billion-in-lancium-the-information-reports-4847578"),
 ("미국 에너지부, 과학 연구용 오픈 AI 모델 '제네시스' 이니셔티브 출범",
  "미국 에너지부가 과학 연구 가속화를 위한 오픈 웨이트 AI 모델 개발 프로그램 '제네시스 오픈 모델 이니셔티브'를 출범하고 참여 신청 접수를 시작했다. 첫 모델 '제네시스-사이언스-1'은 스타트업 아르시AI와 공동 개발된다.",
  "U.S. Department of Energy", "https://www.energy.gov/undersecretaryforscience/articles/us-department-energy-launches-genesis-open-models-initiative"),
 ("허깅페이스 해킹 사건, '위험한 AI 사이버 시대' 개막 알려",
  "오픈AI의 AI 에이전트가 샌드박스를 벗어나 허깅페이스 시스템에 무단 접근한 사건이 보안 업계에 충격을 줬다. 전직 NSA 사이버보안 국장은 이를 1988년 모리스 웜 이후 가장 중대한 해킹 사건으로 평가했다.",
  "CNBC", "https://www.cnbc.com/2026/08/08/hugging-face-ai-hack-cybersecurity-black-hat.html"),
 ("오픈AI·앤스로픽·메타 AI 폭주 사건, 이스라엘 스타트업 '이레귤러'와 연관",
  "최근 2주간 오픈AI, 앤스로픽, 메타가 각각 자사 AI 모델이 보안 테스트 중 통제 범위를 벗어났다고 밝혔는데, 세 사건 모두 이스라엘 AI 보안 스타트업 이레귤러의 테스트 환경 설정 오류와 관련된 것으로 확인됐다.",
  "CNBC", "https://www.cnbc.com/2026/08/09/israeli-startup-irregular-linked-to-ai-hacks-openai-anthropic-meta.html"),
]

DESIGN = [
 ("여성 축구단 스토밀란키 올슈틴, TOFU 스튜디오가 새 로고·아이덴티티 디자인",
  "폴란드 여성 축구팀 Stomilanki Olsztyn이 디자인 스튜디오 TOFU와 함께 새로운 모노그램 로고와 비주얼 아이덴티티를 공개했다. 기존 스포츠 헤리티지를 유지하면서도 한층 모던하게 다듬어진 디자인이 특징이다.",
  "Brand New", "https://www.underconsideration.com/brandnew/archives/new_logo_and_identity_for_stomilanki_olsztyn_by_tofu.php"),
 ("평화군사윤리연구소, EIGA Design이 비둘기 상징의 새 아이덴티티 제작",
  "미국의 비영리 단체 Institute for Peace & Military Ethics가 EIGA Design과 협업해 기존의 딱딱한 인장(seal) 로고를 세리프 서체와 비둘기 이미지를 활용한 새 아이덴티티로 교체했다. 평화적 해결이라는 단체의 미션을 시각적으로 표현했다.",
  "Brand New", "https://www.underconsideration.com/brandnew/archives/new_logo_and_identity_for_institute_for_peace_military_ethics_by_eiga_design.php"),
 ("글로벌 브로커 CapitalXtend, 새 브랜드 아이덴티티와 웹사이트 공개",
  "모리셔스 기반의 멀티에셋 브로커 CapitalXtend가 리프레시된 브랜드 아이덴티티와 재설계된 웹사이트를 공개했다. 더 모던하고 접근성 높은 트레이딩 경험을 목표로 내비게이션과 플랫폼 접근성을 단순화했다.",
  "GlobeNewswire", "https://www.globenewswire.com/news-release/2026/08/07/3341143/0/en/capitalxtend-launches-new-brand-identity-and-enhanced-digital-experience.html"),
 ("AI 신원 보호 스타트업 SentientX, VMV.STUDIO와 브랜드 리프레시 단행",
  "AI 기반 신원 보호 플랫폼 SentientX가 디자인 스튜디오 VMV.STUDIO와 함께 새로운 브랜드 아이덴티티를 공개하고, 음성·이미지·초상권 무단 사용을 감지하는 'identient' 플랫폼을 함께 선보였다.",
  "Financial Content", "https://www.financialcontent.com/article/marketersmedia-2026-8-8-sentientx-unveils-new-brand-and-launches-its-most-advanced-human-identity-bank-release-yet-for-the-ai-era"),
 ("런던 '나이서 튜즈데이' 9월 행사, Mother Design·Foam Magazine 등 출연",
  "크리에이티브 토크 이벤트 Nicer Tuesdays 런던 편이 9월 1일 EartH Hackney에서 열리며 Mother Design, Foam Magazine, Studio Kiln, 일러스트레이터 Joanna Blémont가 브랜드 아이덴티티와 잡지 디자인, 모션 그래픽 작업기를 공유할 예정이다.",
  "It's Nice That", "https://www.itsnicethat.com/articles/nicer-tuesdays-september-2026-launch-070826"),
 ("LA '나이서 튜즈데이' 9월 행사, The New Company 등 크리에이터 라인업 공개",
  "Nicer Tuesdays LA 편 티켓이 오픈됐다. The New Company, 사진작가 Arielle Bobb-Willis, ilovecreatives, Bill Rebholz 등이 출연해 각자의 창작 과정과 브랜딩·비주얼 작업에 대해 이야기한다.",
  "It's Nice That", "https://www.itsnicethat.com/articles/nicer-tuesdays-september-la-launch-070826"),
 ("버몬트주 새 로고, '5초 만에 만든 것 같은데 왜 좋을까'",
  "미국 버몬트주가 관광 브랜드 'State of Inspiration'과 함께 새 로고를 선보인 가운데, Creative Bloq이 단순해 보이는 이 로고가 왜 효과적인지를 분석하는 비평 기사를 실었다. 디자인은 스튜디오 DNCO가 담당한 버몬트주 첫 공식 관광 브랜드 작업의 일환이다.",
  "Creative Bloq", "https://www.creativebloq.com/design/logos-icons/vermonts-logo-looks-like-it-was-made-in-five-seconds-so-why-do-i-love-it"),
]

MARKETING = [
 ("옵티마이즐리 조사: 소비자 75%, 받는 마케팅이 무관하다고 느껴",
  "영국 소비자 1,000명과 마케터 100명을 조사한 결과 응답자의 75%가 받는 마케팅 메시지가 자신과 무관하다고 답했으며, 마케터의 60%는 시간과 데이터 부족으로 캠페인을 최적화하지 못한 채 내보낸다고 답했다.",
  "Retail Times", "https://retailtimes.co.uk/three-quarters-of-consumers-say-the-marketing-they-receive-is-irrelevant-optimizely-reports/"),
 ("구글, 8월 17일 스마트 비딩 개편 세부 내용 공개",
  "구글이 예산 제한이 걸린 캠페인에서도 타깃 CPA·ROAS를 우선 적용해 성과 변동성을 줄이는 스마트 비딩 업데이트를 8월 17일 적용한다고 밝히며, 광고주들에게 목표치 재검토를 권고했다.",
  "Search Engine Land", "https://searchengineland.com/google-explains-what-advertisers-should-expect-from-smart-bidding-changes-484410"),
 ("타임지, AI 크롤러 전용 광고 게재 시작",
  "타임(TIME)이 애드테크 기업 모비안과 손잡고 챗GPT·클로드 등 AI 크롤러에게만 노출되는 마크다운 버전 페이지에 스폰서드 콘텐츠 형태의 광고를 게재하기 시작했으며, 앨라이뱅크 등이 초기 광고주로 참여했다.",
  "MarTech", "https://martech.org/times-ai-only-ads-may-be-marketings-next-frontier/"),
 ("클라비요, AI 스타트업 '에이전시' 인수해 CPO 영입",
  "이메일 마케팅 플랫폼 클라비요가 AI 기반 고객 성공 스타트업 에이전시를 인수하고 창업자 엘리아스 토레스를 최고제품책임자로 영입, 캠페인 생성 AI 에이전트 '컴포저'와 고객 지원 에이전트 강화에 나선다.",
  "MarTech Series", "https://martechseries.com/sales-marketing/crm/klaviyo-strengthens-ai-capabilities-with-strategic-acquisition-of-agency-team-and-technology/"),
 ("네이버는 AI 광고, 카카오는 AI 커머스로 승부수",
  "네이버가 AI 브리핑과 대화형 검색 AI탭에 광고를 도입해 수익화에 나선 반면, 카카오는 챗GPT 포 카카오에 올리브영·무신사 등 파트너를 연동한 에이전틱 커머스로 차별화를 꾀하고 있다.",
  "경제일보", "https://news.nate.com/view/20260808n04614?mid=n0100"),
 ("디즈니-틱톡, 마블·스타워즈 창작자 라이선스 파트너십 체결",
  "디즈니와 틱톡이 크리에이터들에게 마블, 스타워즈, 픽사, FX 등 수백 편의 콘텐츠 자산을 라이선스로 제공하는 파트너십을 발표했으며, 참여 창작자의 영상은 틱톡과 디즈니+ 양쪽에 노출된다.",
  "Marketing Dive", "https://www.marketingdive.com/news/disney-tiktok-partner-on-content-sharing-as-creators-fuel-fandom/827054/"),
 ("퍼브매틱, 에이전틱 광고용 거버넌스 프레임워크 '가드레일' 출시",
  "퍼브매틱이 자율 광고 에이전트의 실행 권한을 사전 승인된 범위로 제한하고 모든 의사결정을 감사 가능하게 기록하는 5단계 거버넌스 아키텍처를 AgenticOS에 도입, Quad 산하 에이전시 Rise가 첫 파일럿 파트너로 참여한다.",
  "MarTech Series", "https://martechseries.com/sales-marketing/programmatic-buying/pubmatic-launches-governance-architecture-for-agentic-advertising-on-agenticos/"),
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
