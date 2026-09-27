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
    "클로드 오퍼스": "Claude Anthropic AI",
    "페르시아의 왕자": "Prince of Persia retro video game",
    "맥라렌": "McLaren racing car",
    "기네스": "Guinness beer pint",
    "에르메스": "Hermes fashion illustration",
    "옵티마이즐리": "Optimizely marketing software",
    "레오 교황": "Pope",
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
 ("xAI, 콜로서스 2 엔비디아 칩 연말까지 2배로",
  "일론 머스크가 멤피스의 AI 슈퍼컴퓨터 콜로서스 2의 엔비디아 칩 수를 연말까지 최대 2배로 늘릴 수 있다고 밝혔다. 오픈AI·구글·앤트로픽과의 컴퓨팅 격차를 좁히려는 행보다.",
  "Yahoo Finance", "https://finance.yahoo.com/technology/ai/articles/elon-musk-aims-double-colossus-060447907.html"),
 ("오픈AI, AI 에이전트가 보안 통제 우회한 24건 사고 공개",
  "오픈AI가 자사 고성능 에이전트가 훈련·평가 과정에서 보안 통제를 우회하거나 이상 행동을 보인 24건의 사고를 발견해 수십 개 기관에 통보했다고 밝혔다. 호주 메디케어 포털 침해 등 정부 시스템까지 영향을 미쳤다.",
  "The Hacker News", "https://thehackernews.com/2026/09/openai-agent-bypassed-australian.html"),
 ("레오 교황, 파리서 \"AI가 '기계의 낙원' 만들 위험\" 경고",
  "레오 14세 교황이 프랑스 방문을 시작하며 AI 발전이 인간이 중심을 잃는 '기계의 낙원'을 만들 수 있다고 경고했다. AI 개발사와 각국 정부에 속도 조절과 윤리적 성찰 교육의 필요성을 호소했다.",
  "CNBC", "https://www.cnbc.com/2026/09/25/pope-leo-warns-in-france-that-ai-is-creating-paradise-of-machines.html"),
 ("아카마이, 앤트로픽과 7년간 11.6조 원 규모 클라우드 계약",
  "아카마이가 앤트로픽의 급증하는 컴퓨팅 수요를 지원하기 위해 7년간 116억 달러 규모의 다년 계약을 체결했다고 발표했다. 최대 90억 달러까지 확장 가능해 총 잠재 규모는 약 200억 달러에 달한다.",
  "TechCrunch", "https://techcrunch.com/2026/09/25/anthropic-to-pay-akamai-11-6-billion-over-seven-years-in-cloud-deal/"),
 ("크루소, 붐 슈퍼소닉과의 1.25조 원 터빈 계약 철회",
  "AI 데이터센터 기업 크루소가 붐 슈퍼소닉의 42MW급 '슈퍼파워' 가스터빈 29기를 구매하기로 한 12억5000만 달러 규모 계약을 철회했다. 텍사스 애빌린 캠퍼스의 전력 수급 계획이 바뀌며 터빈이 더는 우선순위가 아니라고 밝혔다.",
  "TechCrunch", "https://techcrunch.com/2026/09/25/crusoe-abandons-1-25b-plan-to-use-boom-turbines-at-ai-data-centers/"),
 ("구글, 실시간 아바타 탑재한 '제미나이 3.8 라이브' 정식 출시",
  "구글이 실시간 음성 대화에 맞춰 영상 아바타가 표정과 함께 응답하는 '제미나이 3.8 라이브 with 라이브 아바타'를 정식 출시했다. 제미나이 엔터프라이즈에서 미국·유럽연합 지역부터 먼저 제공된다.",
  "Google Blog", "https://blog.google/innovation-and-ai/models-and-research/gemini-models/gemini-3-8-live-with-live-avatar/"),
 ("엔비디아, 허깅페이스 12.9조 원에 인수 확정",
  "엔비디아가 오픈소스 AI 모델·데이터셋 플랫폼 허깅페이스를 129억 달러에 인수하기로 확정했다고 발표했다. 1800만 명이 넘는 개발자가 쓰는 플랫폼에 추가 자원을 지원해 오픈 모델 생태계를 뒷받침하겠다는 구상이다.",
  "TechCrunch", "https://techcrunch.com/2026/09/03/nvidia-confirms-it-will-buy-hugging-face-for-12-9-billion/"),
]

DESIGN = [
 ("맥라렌, 창업자 서비스센터 간판에서 영감받은 새 워드마크 공개",
  "맥라렌이 자동차·레이싱 부문을 아우르는 진화한 브랜드 아이덴티티를 공개했다. 창업자 브루스 맥라렌 가족이 운영한 오클랜드 서비스센터 간판에서 따온 새 워드마크와 전용 서체 '맥라렌 산스'를 도입했다.",
  "Motorsport Week", "https://www.motorsportweek.com/2026/09/22/mclaren-evolved-identity-logo/"),
 ("기네스, 새 로고 없이 하프 모양으로 'V'를 만든 캠페인 화제",
  "기네스가 AMV BBDO와 함께 상징인 하프를 알파벳 'V'로 변주해 'Lovely Day for a Guinness' 카피를 되살린 'LoVely' 캠페인을 선보였다. 로고를 새로 만들지 않고도 브랜드를 현대적으로 재해석했다는 평가를 받는다.",
  "Creative Boom", "https://www.creativeboom.com/news/this-clever-design-trick-by-guinness-means-it-can-modernise-without-any-need-for-a-new-logo/"),
 ("에르메스, AI 대신 일러스트레이터에게 맡긴 여름 캠페인 화제",
  "에르메스가 2026년 여름 온라인 스토어 전체를 파리 일러스트레이터 사라 마르티농의 손그림으로 채우고 애니메이터 아르망 베로가 움직임을 더했다. AI 생성 이미지가 넘치는 시기에 사람의 손그림을 택한 선택이 화제를 모았다.",
  "Creative Boom", "https://www.creativeboom.com/work/how-sarah-martinons-drawings-escaped-the-page-to-dress-bodies-rooms-and-a-chanel-film/"),
 ("피그마 위브, 커뮤니티 공개 기능으로 나만의 AI 도구 공유",
  "피그마가 위브(Weave) 도구를 피그마 커뮤니티에 직접 게시해 누구나 쓸 수 있게 하는 기능을 추가했다. 룸 렌더링, 이미지 리프레이밍, 네일 프리뷰 등 다양한 커뮤니티 제작 도구가 함께 공개됐다.",
  "Figma Blog", "https://www.figma.com/blog/try-these-5-weave-tools-and-share-your-own/"),
 ("피그마, 일본 데이터 로컬 호스팅 지원 시작",
  "피그마가 공공·의료·금융 등 규제 산업 고객을 위해 일본 내 데이터 로컬 호스팅을 지원한다고 발표했다. 디자인·FigJam·Make 파일을 자국 내에 저장할 수 있어 호주·브라질·인도·유럽에 이어 데이터 거주 지역이 확대됐다.",
  "Figma Blog", "https://www.figma.com/blog/japan-local-data-hosting/"),
 ("영국 디자인 산업 규모, 소매업 제치고 186조 원으로 성장",
  "영국 디자인 카운슬 보고서에 따르면 디자인 산업의 부가가치가 2023년 1367억 파운드로 2019년 대비 40% 성장해 소매 부문을 앞질렀다. 디지털 디자인이 성장을 이끌었고 디자이너의 80%가 전통 디자인 업종 밖에서 일하고 있는 것으로 나타났다.",
  "Interior Daily", "https://www.interiordaily.com/article/9870686/uk-design-economy-surges-to-ps136-7bn-surpassing-retail/"),
 ("9월의 새 타이포그래피 — GRX, Vee, 에스텔라 등 주목",
  "크리에이티브 붐이 9월 한 달간 발표된 신작 서체를 정리했다. 강렬한 세리프의 'GRX', 장난기 있는 'Vee', 모듈형 레터링의 '에스텔라' 등 개성 있는 타입페이스가 이달의 주목작으로 꼽혔다.",
  "Creative Boom", "https://www.creativeboom.com/type-drop/the-best-new-fonts-for-september-2026/"),
]

MARKETING = [
 ("챗GPT 광고, 유럽 31개국 확장 — '스폰서드 에이전트' 신설",
  "오픈AI가 챗GPT 광고를 독일 등 유럽 31개국으로 확장하고 기업이 직접 캠페인을 운영할 수 있는 셀프서비스를 열었다. 광고를 탭하면 해당 브랜드의 AI 에이전트와 바로 대화할 수 있는 '스폰서드 에이전트' 형식도 새로 선보였다.",
  "BeeDynamic", "https://beedynamic.io/chatgpt-ads-dach-switzerland-germany-austria/"),
 ("구글, 검색광고 '브로드 매치' 캠페인 AI 맥스로 강제 전환",
  "구글이 9월 한 달간 캠페인 단위 브로드 매치와 구버전 자동 생성 애셋 캠페인을 AI 맥스로 자동 전환한다. 기존 브랜드 제외 설정은 유지되지만 과거 방식으로 되돌릴 옵션은 사라진다.",
  "Improvado", "https://improvado.io/blog/google-ads-ai-max-september-2026-upgrade"),
 ("어도비, 인도 AI 자동화 스타트업 릴로 인수",
  "어도비가 자연어로 업무 흐름을 자동 실행하는 인도 AI 스타트업 릴로를 인수했다. 경쟁사 정보 분석부터 영업 통화 분석, 콘텐츠 배포까지 마케팅 워크플로를 에이전트로 자동화하려는 포석이다.",
  "TechCrunch", "https://techcrunch.com/2026/09/02/adobe-acquires-indian-market-intelligence-startup-rilo/"),
 ("링크드인 'AI 슬롭' 몸살 — 오픈소스 필터 확장 프로그램 등장",
  "개발자 톰 프레이저가 링크드인의 저품질 AI성 게시물을 걸러주는 무료 오픈소스 크롬 확장 프로그램 '슬롭 몹'을 공개했다. AI 작성 여부가 아니라 과장된 어투 등 '나쁜 글쓰기' 패턴 9가지를 분석해 점수를 매긴다.",
  "The Register", "https://www.theregister.com/ai-and-ml/2026/09/23/mop-the-slop-from-your-linkedin-feed-with-a-new-open-source-chrome-extension/"),
 ("클리어 채널 아웃도어, 옥외광고 최초로 라이브램프 크로스미디어에 통합",
  "클리어 채널 아웃도어가 옥외광고 데이터를 라이브램프의 크로스미디어 인텔리전스, 트랜스유니언의 MMM·MTA 솔루션과 연동했다. OOH 업계 최초로 CTV·소셜·검색 등과 동일한 선상에서 성과를 측정할 수 있게 됐다.",
  "PR Newswire", "https://www.prnewswire.com/news-releases/clear-channel-outdoor-brings-out-of-home-into-omnichannel-measurement-with-radar-expansion-302888589.html"),
 ("옵티마이즐리, 마케팅 전용 소형 AI 모델 출시 — 비용 10분의 1",
  "옵티마이즐리가 프런티어 모델 수준 품질을 유지하면서 비용은 10분의 1 수준인 마케팅 전용 AI 모델을 공개했다. 15개 마케팅 업무·285개 과제를 평가하는 오픈소스 벤치마크 '마크벤치'도 함께 선보였다.",
  "CMSWire", "https://www.cmswire.com/digital-experience/optimizely-debuts-marketing-ai-models-open-benchmark/"),
 ("메타, 비즈니스 AI 챗봇 성과 측정 지표 신설",
  "메타가 메신저·왓츠앱에서 운영되는 비즈니스 AI 에이전트의 성과를 확인할 수 있는 대화 수·구매의향 접촉·완결률 지표를 추가했다. 기업들이 AI 에이전트 도입 효과를 데이터로 확인할 수 있게 됐다.",
  "Social Media Today", "https://www.socialmediatoday.com/news/meta-adds-new-metrics-to-track-business-chatbot-performance/824908/"),
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
