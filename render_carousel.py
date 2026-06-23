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
 ("OpenAI, 오픈소스 보안 이니셔티브 'Patch the Planet' 발표",
  "OpenAI가 Trail of Bits·HackerOne와 함께 오픈소스 취약점을 자동 탐지·패치하는 'Patch the Planet'을 발표했다. 첫 주 만에 cURL·Python·Go 등 19개 프로젝트에서 수백 개 버그를 발견하고 64개 PR을 제출했다.",
  "TechCrunch", "https://techcrunch.com/2026/06/22/openai-launches-new-initiative-to-help-find-and-patch-open-source-bugs/"),
 ("퀄컴, AI 스타트업 Modular 40억 달러 인수 협상",
  "퀄컴이 AI 소프트웨어 스택 스타트업 Modular를 약 40억 달러에 인수하는 협상을 진행 중이라고 블룸버그가 보도했다. 9개월 전 밸류에이션 대비 2.5배 상승한 가격으로, 엔비디아 대항 데이터센터 AI 칩 전략의 핵심이다.",
  "Bloomberg", "https://www.bloomberg.com/news/articles/2026-06-22/qualcomm-is-said-to-near-deal-for-ai-chip-startup-modular"),
 ("오라클, AI 도입으로 직원 2만1천 명 감축",
  "오라클이 지난 12개월 동안 글로벌 인력의 13%에 해당하는 2만1천 명을 감원했다고 블룸버그가 보도했다. AI·클라우드 사업 확장에 집중하며 구조조정 비용으로만 18억4천만 달러를 지출한 것으로 나타났다.",
  "Bloomberg", "https://www.bloomberg.com/news/articles/2026-06-22/oracle-layoffs-fueled-by-ai-reduces-workforce-by-21-000"),
 ("트럼프, 양자컴퓨팅 행정명령 서명 — 2028년 QC 배치 목표",
  "트럼프 대통령이 6월 22일 양자컴퓨팅 혁신 가속화를 위한 행정명령 2건에 서명했다. 2028년까지 국가 연구소에 과학적으로 유의미한 양자컴퓨터를 배치하고, 2030년까지 정부 시스템을 양자 내성 암호화로 전환하는 것이 목표다.",
  "White House", "https://www.whitehouse.gov/presidential-actions/2026/06/ushering-in-the-next-frontier-of-quantum-innovation/"),
 ("Anthropic, 서울 사무소 개설·한국 정부와 AI 안전 MOU",
  "Anthropic이 서울에 아태지역 3번째 사무소를 개설하고 한국 과학기술정보통신부와 AI 안전 협력 MOU를 체결했다. NAVER 전 개발자 조직이 Claude Code를 도입하는 등 국내 주요 기업과의 파트너십도 잇따라 발표됐다.",
  "Anthropic", "https://www.anthropic.com/news/seoul-office-partnerships-korean-ai-ecosystem"),
 ("SpaceX, AI 코딩 스타트업 Cursor 600억 달러에 인수",
  "스페이스X가 AI 코딩 도구 Cursor를 600억 달러(약 84조원) 주식 교환 방식으로 인수한다. Cursor의 연간 반복 매출은 40억 달러를 돌파했으며 IPO 직후 전격 발표된 이 딜은 에이전틱 코딩 시대의 지각변동을 상징한다.",
  "TechCrunch", "https://techcrunch.com/2026/06/16/spacex-to-acquire-cursor-for-60b-in-stock-days-after-blockbuster-ipo/"),
 ("GPT-5.6, ChatGPT 내부 테스트 개시 — 출시 임박",
  "OpenAI 차세대 모델 GPT-5.6이 ChatGPT 내에서 은밀하게 테스트 중인 것으로 알려졌다. 기존 GPT-5.5 대비 50% 더 긴 150만 토큰 컨텍스트를 지원하며, 예측 시장 Polymarket은 6월 22~28일 출시 확률을 90%로 집계했다.",
  "Memeburn", "https://memeburn.com/what-is-gpt-5-6-openais-next-ai-model-is-already-being-tested-inside-chatgpt/"),
]

DESIGN = [
 ("KFC, JKR과 함께 'Bucketverse' 브랜드 세계관 출범",
  "KFC가 크리에이티브 에이전시 JKR과 함께 로고·타이포그래피·매장·패키징·앱을 아우르는 360도 전방위 리브랜드를 단행했다. 닭 버킷을 중심으로 한 고유 브랜드 세계관 'Bucketverse'를 구축, 아이콘을 하나의 확장 가능한 세계로 만든 것이 핵심이다.",
  "It's Nice That", "https://www.itsnicethat.com/articles/jkr-kfc-graphic-design-project-150626"),
 ("칸 라이언즈 2026: 'Creative Brand Lion' 신설 + AI Craft 부문 도입",
  "칸 라이언즈 2026이 창의적 역량을 일관되게 발휘하는 브랜드를 별도 시상하는 'Creative Brand Lion'을 신설했다. 인간 창의성과 AI 협업 결과물을 전용 시상하는 AI Craft 서브카테고리도 Design·Film·Digital Craft 등 5개 부문에 걸쳐 도입됐다.",
  "Haute Living", "https://hauteliving.com/2026/06/cannes-lions-2026-introduces-the-creative-brand-lion-and-goes-all-in-on-ai/790426/"),
 ("노턴 미술관, Koto의 80년 전 워드마크 부활 리브랜드",
  "디자인 스튜디오 Koto가 플로리다주 노턴 미술관 리브랜드를 진행하며 약 80년 전 원본 워드마크를 현대적으로 복원했다. 고전 타이포그래피를 중심으로 한 새 아이덴티티는 미술관의 역사적 권위와 현대적 접근성을 동시에 전달한다는 평을 받고 있다.",
  "It's Nice That", "https://www.itsnicethat.com/articles/koto-norton-museum-of-art-graphic-design-project-030626"),
 ("Equals, SomeOne과 함께 새 브랜드 아이덴티티 공개",
  "결제 솔루션 기업 Equals가 스튜디오 SomeOne과 함께 새 로고 및 브랜드 아이덴티티를 공개했다. 평등과 신뢰라는 브랜드 가치를 명확한 시각 언어로 구현한 이번 개편은 금융 서비스 브랜딩의 정석이라는 평을 받고 있다.",
  "Brand New", "https://www.underconsideration.com/brandnew/archives/new_logo_and_identity_for_equals_by_someone.php"),
 ("NBA 미네소타 팀버울브스, 새 로고 & 유니폼 공개",
  "NBA 미네소타 팀버울브스가 새 로고와 유니폼 디자인을 공개했다. 팀 정체성을 강화하는 방향으로 핵심 시각 요소를 정교하게 다듬은 이번 개편은 팬들 사이에서 긍정적 반응을 얻고 있다.",
  "Brand New", "https://www.underconsideration.com/brandnew/archives/new_logo_and_uniforms_for_minnesota_timberwolves.php"),
 ("Creative Boom 창간 15주년 리뉴얼 — 크리에이티비티의 재정의",
  "크리에이티브 미디어 Creative Boom이 창간 15주년을 맞아 새로운 일러스트레이션 시리즈와 함께 사이트 전체 디자인을 전면 개편했다. 영국 창의 산업을 대표하는 매체로서의 새로운 정체성을 시각적으로 표현한 리뉴얼이다.",
  "Creative Boom", "https://www.creativeboom.com/news/a-new-creative-boom-launches/"),
 ("\"이상해지는 것이 브랜딩을 구한다\" — 2026 브랜딩 키워드",
  "AI가 쏟아내는 '무난하고 안전한' 브랜딩에 반발해 독특함과 개성을 강조하는 기업이 늘고 있다는 분석이 나왔다. '바이럴보다 기억에 남는 이상함'이 2026년 브랜딩의 핵심 키워드로 부상하고 있다.",
  "Creative Boom", "https://www.creativeboom.com/insight/how-being-weird-can-save-branding-in-2026/"),
]

MARKETING = [
 ("칸 라이언즈 2026 개막: 6월 22~26일 크리에이티비티 축제",
  "칸 라이언즈 2026이 6월 22일 프랑스 칸에서 개막했다. 총 2만50건의 출품작이 접수됐으며 AI 크래프트 신설 부문과 Oprah Winfrey의 LionHeart 수상 등 주요 화제를 품고 있다.",
  "BestMediaInfo", "https://bestmediainfo.com/mediainfo/advertising/cannes-lions-2026-day-1-india-wins-2-silver-and-2-bronze-12062893"),
 ("R/GA at Cannes: \"AI 시대, 크리에이티비티의 가치는 더 높아졌다\"",
  "R/GA 경영진이 칸 라이언즈에서 AI가 콘텐츠를 범람시킬수록 진정한 창의성은 더 희귀하고 가치 있어진다고 역설했다. 브랜드는 이제 캠페인 자산 생산을 넘어 지속적 경험과 참여를 창출하는 시스템을 구축해야 한다고 강조했다.",
  "BestMediaInfo", "https://bestmediainfo.com/mediainfo/advertising/as-ai-floods-the-market-with-content-rga-says-creativity-is-becoming-more-valuable-12062704"),
 ("AB InBev CMO: \"바이럴에 현혹되지 마라, 비즈니스 문제를 해결하라\"",
  "AB InBev 글로벌 CMO 마르셀 마르콘데스가 칸 라이언즈에서 '쿨해 보이는 작업'에 현혹되지 말고 비즈니스·소비자 문제를 해결하는 인간 중심 크리에이티비티에 집중해야 한다고 강조했다. 전략적 일관성 없이 바이럴만 좇으면 장기적으로 브랜드가 약화된다는 경고다.",
  "BestMediaInfo", "https://bestmediainfo.com/mediainfo/mediainfo-marketing/dont-get-seduced-by-cool-work-that-people-talk-about-ab-inbevs-marcel-marcondes-at-cannes-2026-12062456"),
 ("TAM·VTION, CTV 광고 측정 솔루션 'CTV Ad Pulse' 출시",
  "TAM과 VTION이 IPL 2026 시즌부터 커넥티드TV 광고 측정 솔루션 'CTV Ad Pulse'를 출시한다. TAM의 광고 모니터링과 VTION의 CTV 시청자 분석을 결합해 오디언스 프로파일링·도달 범위·빈도·게재 지표를 스트리밍 플랫폼에 실시간 제공한다.",
  "BestMediaInfo", "https://bestmediainfo.com/mediainfo/mediainfo-digital/tam-vtion-launch-ctv-ad-pulse-beginning-ipl-2026-campaign-measurement-12061994"),
 ("플립카트: \"셀럽 아닌 일반 크리에이터 릴이 뷰티 매출 이끈다\"",
  "인도 이커머스 플랫폼 플립카트가 뷰티 카테고리 매출을 이끄는 것은 유명 연예인이 아니라 일상적 크리에이터의 바이럴 릴스라고 밝혔다. 플립카트는 이 추세를 'Glam Up Fest'로 공략하며 비메트로 인도 시장 확장에 집중하고 있다.",
  "BestMediaInfo", "https://bestmediainfo.com/mediainfo/mediainfo-digital/not-celebrities-but-a-viral-reel-can-move-beauty-sales-by-the-minute-says-flipkart-12060948"),
 ("구글, AI 오버뷰 업데이트로 퍼블리셔 클릭률 회복 시도",
  "구글이 AI 오버뷰에 인라인 링크와 '구독' 라벨을 추가하는 업데이트를 발표했다. AI 요약이 검색 유입을 58% 감소시켰다는 비판에 대응하는 조치로, 마케터들에게는 콘텐츠 SEO 전략 재검토를 요구하는 변화다.",
  "Seafoam Media", "https://seafoammedia.com/june-2026-marketing-news-trends-insights/"),
 ("봄베이 쉐이빙 컴퍼니 CEO, WhatsApp 마케팅 스팸 논란에 사과",
  "봄베이 쉐이빙 컴퍼니 CEO 샨타누 데시판데가 과도한 WhatsApp 프로모션 메시지 논란에 공개 사과했다. 이 사건은 D2C 브랜드의 디지털 마케팅 과잉 소통에 대한 소비자 반발을 재조명하며 고객 커뮤니케이션 윤리 논의를 촉발시켰다.",
  "BestMediaInfo", "https://bestmediainfo.com/mediainfo/mediainfo-digital/bombay-shaving-company-ceo-apologises-after-entrepreneur-flags-whatsapp-spam-12060996"),
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
