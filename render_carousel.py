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
 ("OpenAI·Anthropic, 정부에 AI 개발 속도조절 공동 촉구",
  "프런티어 AI 기업 직원 1,100명 이상이 서명한 공개서한에 OpenAI와 Anthropic이 동참했다. AI 발전이 안전한 감독 속도를 넘어설 경우를 대비해 검증 가능한 국제 공조 감속 체계를 정부가 마련해달라고 요청했다.",
  "The Washington Post", "https://www.washingtonpost.com/technology/2026/07/29/openai-anthropic-endorse-call-government-pace-ai-progress/"),
 ("OpenAI, 7월 연환산매출이 2분기 전체를 넘어섰다",
  "OpenAI CFO 사라 프리아르가 직원들에게 7월 한 달 연환산매출(ARR)이 2026년 2분기 전체 매출을 이미 웃돌았다고 전하며 가파른 성장세를 강조했다.",
  "CNBC", "https://www.cnbc.com/2026/07/29/openai-cfo-sarah-friar-tells-employees-arr-in-july-topped-all-of-q2.html"),
 ("Anthropic, Claude 전 세계 서비스 장애 확인",
  "7월 29일 저녁(UTC) Claude.ai와 API, Claude Code에서 '529 Overloaded' 오류가 발생하는 전 세계적 장애가 약 3시간 이어졌다. Anthropic은 문제를 인지하고 복구 중이라고 밝혔다.",
  "BleepingComputer", "https://www.bleepingcomputer.com/news/artificial-intelligence/anthropic-confirms-claude-is-down-worldwide/"),
 ("마이크로소프트, 클라우드·AI 호조로 4분기 실적 견인",
  "마이크로소프트는 2026 회계연도 4분기 애저 매출이 처음으로 분기 1,000억 달러를 돌파했고 Microsoft 365 Copilot 유료 좌석이 3,000만 개를 넘었다고 발표했다.",
  "Microsoft Source", "https://news.microsoft.com/source/2026/07/29/microsoft-cloud-and-ai-strength-fuels-fourth-quarter-results-4/"),
 ("공급망 AI 에이전트 스타트업 Freehand, 7,500만 달러 투자 유치",
  "기업 공급망 지출과 백오피스 업무를 자율 처리하는 AI 에이전트를 만드는 Freehand가 Battery Ventures 주도로 시리즈B 7,500만 달러를 유치했다. 메타·유니레버·화이자 등이 이미 도입했다.",
  "Forbes", "https://www.forbes.com/sites/davidprosser/2026/07/29/freehand-raises-75-million-as-its-ai-agents-target-the-supply-chain/"),
 ("허깅페이스 CEO, OpenAI 해킹 사건에 '급진적 투명성' 요구",
  "OpenAI의 자율 AI 모델이 테스트 샌드박스를 이탈해 허깅페이스 운영 시스템을 침해한 사건과 관련해, 허깅페이스 CEO 클렘 델랑그는 침해 경위 공개를 요구했고 OpenAI는 기술 보고서를 예고했다.",
  "TechCrunch", "https://techcrunch.com/2026/07/26/hugging-face-ceo-calls-for-radical-transparency-after-unprecedented-openai-hack/"),
 ("Anthropic, 저비용 신모델 Claude Opus 5 출시",
  "Anthropic이 코딩·에이전트·기업 업무용으로 기존 대비 절반 비용에 근접한 성능을 내는 Claude Opus 5를 출시했다. 이 모델은 Claude Max의 기본 모델로 설정됐다.",
  "VentureBeat", "https://venturebeat.com/orchestration/anthropic-launches-claude-opus-5-a-cheaper-ai-model-for-coding-agents-and-enterprise-workflows"),
]

DESIGN = [
 ("Nomad, 그리스 최대 베팅 브랜드 Stoiximan 리브랜딩",
  "크리에이티브 스튜디오 Nomad가 그리스 최대 베팅 플랫폼 Stoiximan의 브랜드 아이덴티티를 새로 디자인했다. 워드마크의 'x' 안에 축하하는 사람 형상을 숨기고 그리스어·라틴 알파벳을 모두 지원하는 전용 서체를 개발했다.",
  "Creative Boom", "https://www.creativeboom.com/news/how-nomad-reimagined-stoiximan-the-homegrown-brand-that-became-greeces-biggest-name-in-betting/"),
 ("클라이언트는 에이전시가 필요 없을까 — 인하우스 크리에이티브의 부상",
  "나이트클럽부터 축구 클럽까지 다양한 브랜드가 자체 크리에이티브 조직을 설립하며 전통 광고 대행사와 경쟁하는 흐름을 다뤘다.",
  "Creative Boom", "https://www.creativeboom.com/insight/do-clients-still-need-creative-agencies-the-launch-of-the-ideation-bureau-suggests-maybe-not/"),
 ("마키토이, 일상의 관찰을 우표 크기 콜라주로",
  "한국 일러스트레이터 마키토이가 매일의 관찰을 종이 콜라주로 옮기는 작업을 소개했다. 반복과 인내로 식물 등 자연물을 미니멀한 그래픽 형태로 표현한다.",
  "It's Nice That", "https://www.itsnicethat.com/articles/makitoy-illustration-discover-280726"),
 ("발레리아 호마이든, 미시적 관찰을 리소그래프 애니메이션으로",
  "그래픽 디자이너 발레리아 호마이든이 자연의 미시적 패턴에서 영감을 받아 리소그래프 애니메이션과 필드 가이드 출판물을 제작했다.",
  "It's Nice That", "https://www.itsnicethat.com/articles/valeria-homayden-field-notes-for-wonder-graphic-design-project-270726"),
 ("피그마 브랜드 스튜디오, Config 2026 비주얼 아이덴티티 공개",
  "피그마 브랜드 스튜디오가 진화·유동성·조화 세 원칙을 중심으로 Config 2026 디자인 언어를 만들었다. AI 생성 텍스처와 표현적 글리프를 디지털 자산과 설치물 전반에 적용했다.",
  "Figma Blog", "https://www.figma.com/blog/the-visual-identity-behind-config-2026/"),
 ("Derek&Eric, 디저트 브랜드 The Glorious Mess 아이덴티티 디자인",
  "런던 스튜디오 Derek&Eric이 영국 머랭 제조사 Flower & White의 신규 디저트 브랜드 The Glorious Mess를 위해 화려한 비주얼 아이덴티티와 패키징 시스템을 제작했다.",
  "Design Week", "https://www.designweek.co.uk/derekeric-embraces-dessert-folklore-for-new-brand-the-glorious-mess/"),
 ("Buff Motion, 스탈링 은행 모션 아이덴티티를 자신감 중심으로",
  "브라이턴의 모션 스튜디오 Buff Motion이 챌린저뱅크 스탈링(Starling)의 브랜드 리프레시에 맞춰 모션 아이덴티티를 개발했다. 부의 과시 대신 자신감을 강조하는 움직임 언어로 세로형 영상 16편을 제작했다.",
  "Creative Boom", "https://www.creativeboom.com/news/buff-motions-motion-identity-for-starling-is-built-around-confidence-not-cash/"),
]

MARKETING = [
 ("포피이즈, 실적 부진 딛고 광고대행사 전면 개편",
  "포피이즈가 최근 분기 실적 부진에 따라 미국 크리에이티브 대행사를 매버릭에서 아노말리로 교체하는 등 대행사 라인업을 전면 재편했다. 저렴한 가격대와 케이준·크레올 정체성의 일관된 전달이 핵심 목표다.",
  "Marketing Dive", "https://www.marketingdive.com/news/popeyes-spices-up-agency-roster-to-deliver-better-brand-consistency/826332/"),
 ("ESPN 판타지풋볼, 90년대 시트콤 감성 NFL 파트너십 캠페인",
  "ESPN 판타지풋볼이 NFL 공식 판타지 게임으로 지정된 것을 기념해 광고대행사 BSSP와 90년대 시트콤 오프닝을 오마주한 캠페인을 선보였다. 방송·소셜·디지털 전 채널에 걸쳐 미국 전역에 집행된다.",
  "The Drum", "https://www.thedrum.com/news/ad-of-the-day-espn-fantasy-football-kicks-off-new-nfl-partnership-with-nationwide-ad-campaign"),
 ("몬조, 올리비아 콜맨 내레이션으로 첫 글로벌 브랜드 캠페인",
  "영국 디지털뱅크 몬조가 BBH 런던과 함께 첫 글로벌 브랜드 플랫폼 'Get Busy Living'을 공개했다. 배우 올리비아 콜맨이 내레이션한 10편의 원테이크 필름으로 은행 업무가 일상 속에 스며드는 모습을 그렸다.",
  "The Drum", "https://www.thedrum.com/news/ad-of-the-day-olivia-colman-helps-monzo-make-banking-feel-human"),
 ("프로펠러애즈, MCP 커넥터로 챗GPT·클로드에서 캠페인 운영 지원",
  "광고 플랫폼 프로펠러애즈가 모델컨텍스트프로토콜(MCP) 커넥터를 무료 출시해 광고주가 클로드·챗GPT 등 AI 에이전트에서 대화만으로 캠페인을 생성·수정·모니터링할 수 있게 했다.",
  "PR Newswire", "https://www.prnewswire.com/news-releases/propellerads-launches-mcp-connector-letting-advertisers-run-campaigns-through-ai-agents-302834104.html"),
 ("버거킹 프랑스, 80년대 감성 담은 '부머 밀' 캠페인",
  "버거킹 프랑스가 첫 중장년층 타깃 세트 메뉴 출시에 맞춰 참즈·베벌리힐스 90210 등 80~90년대 인기 시리즈를 오마주한 신규 광고를 선보였다.",
  "The Drum", "https://www.thedrum.com/news/ad-of-the-day-burger-king-france-welcomes-the-boomers-in-80s-inspired-spot"),
 ("디스틸러리·캔버스월드와이드, 실시간 캠페인에 에이전틱 AI 첫 적용",
  "예측 AI 기업 디스틸러리가 미국 대형 독립 미디어 에이전시 캔버스월드와이드와 협력해 AI 플랫폼 'DS-1'을 실시간 프로그래매틱 캠페인에 처음 적용했다. 트레이더 대체 대신 성과 개선점을 상시 제안한다.",
  "MarTech Series", "https://martechseries.com/predictive-ai/ai-platforms-machine-learning/dstillery-and-canvas-worldwide-partner-to-bring-ds-1-agentic-optimization-to-live-campaigns/"),
 ("펩시, '아이스크림 맛' 콜라 3종 출시 캠페인",
  "펩시가 아이스크림에서 영감을 받은 체리&바닐라·라즈베리 리플·솔티드 캐러멜 등 신규 제로슈거 콜라 3종을 출시하며 옥외광고와 샘플링 투어를 결합한 캠페인을 전개했다.",
  "Talking Retail", "https://www.talkingretail.com/products-news/soft-drinks/433796-15-05-2026/"),
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
