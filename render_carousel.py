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

# ---------------------------------------------------------------- 글래스모피즘 (Apple Liquid Glass 스타일)
def _round_mask(w, h, radius):
    m = Image.new("L", (w, h), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, w-1, h-1], radius=radius, fill=255)
    return m

def glass_layer(im, box, radius=36, blur=18, tint=(255, 255, 255, 120),
                border=(255, 255, 255, 170), shadow=True):
    """box 영역의 배경을 블러+반투명 화이트로 덮어 '프로스티드 글래스' 패널을 만든다.
    Apple Liquid Glass처럼 ① 배경 블러 ② 화이트 틴트 ③ 상단 하이라이트 ④ 미세 보더 ⑤ 소프트 섀도."""
    x0, y0, x1, y1 = [int(v) for v in box]
    x0 = max(0, x0); y0 = max(0, y0); x1 = min(W, x1); y1 = min(H, y1)
    w, h = x1-x0, y1-y0
    if w <= 0 or h <= 0:
        return im
    base = im.convert("RGBA")
    # ⑤ 소프트 드롭 섀도 (패널을 살짝 띄움)
    if shadow:
        sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(sh).rounded_rectangle([x0, y0+8, x1, y1+10], radius=radius, fill=(40, 40, 50, 70))
        base = Image.alpha_composite(base, sh.filter(ImageFilter.GaussianBlur(18)))
    # ① 배경 블러
    region = base.crop((x0, y0, x1, y1)).convert("RGB").filter(ImageFilter.GaussianBlur(blur))
    glass = region.convert("RGBA")
    # ② 화이트 틴트
    glass = Image.alpha_composite(glass, Image.new("RGBA", (w, h), tint))
    # ③ 상단 → 하단 화이트 하이라이트 그라데이션 (유리 광택)
    grad = Image.new("L", (1, h))
    for i in range(h):
        grad.putpixel((0, i), int(70 * (1 - i / h)))
    grad = grad.resize((w, h))
    hl = Image.new("RGBA", (w, h), (255, 255, 255, 0)); hl.putalpha(grad)
    glass = Image.alpha_composite(glass, hl)
    # ④ 미세 보더 (1.5px 화이트)
    ImageDraw.Draw(glass).rounded_rectangle([0, 0, w-1, h-1], radius=radius, outline=border, width=2)
    base.paste(glass, (x0, y0), _round_mask(w, h, radius))
    return base.convert("RGB")

def glass_pill(im, x, y, text, f, fg=WHITE, px=28, py=15, dot=None):
    """이미지 위에 얹는 프로스티드 글래스 pill. dot=accent색이면 좌측에 컬러 점."""
    d0 = ImageDraw.Draw(im)
    pad_l = px + (26 if dot else 0)
    tw = d0.textlength(text, font=f); a, de = f.getmetrics(); th = a + de
    w = int(tw + pad_l + px); h = int(th + py*2)
    im = glass_layer(im, (x, y, x+w, y+h), radius=h//2, blur=16,
                     tint=(255, 255, 255, 90), shadow=False)
    d0 = ImageDraw.Draw(im)
    tx = x + pad_l
    if dot:
        cy = y + h//2
        d0.ellipse([x+px-2, cy-7, x+px+12, cy+7], fill=dot)
    d0.text((tx, y + py - 2), text, font=f, fill=fg)
    return im, x + w

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

def card(idx, total, cat_en, cat_ko, ac, title, body, source, url, fn, force_search=False, img_url=None):
    im, d = base(CREAM)
    BH = 640
    # img_url: og:image를 가져올 원본 기사 URL(이미지 관련성 보존). url: 화면에 노출/클릭되는 검증된 링크.
    img = get_card_image(img_url or url, ac, source, W, BH, fn.split(".")[0], title, cat_en, force_search)
    im.paste(img, (0, 0))
    # 이미지 위 그라데이션(상·하단 가독성)
    shade = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shade)
    for i in range(180):
        sd.line([(0, i), (W, i)], fill=(0, 0, 0, int(110*(1-i/180))))            # 상단
    for i in range(220):
        sd.line([(0, BH-1-i), (W, BH-1-i)], fill=(0, 0, 0, int(90*(1-i/220))))   # 하단
    im = Image.alpha_composite(im.convert("RGBA"), shade).convert("RGB")
    d = ImageDraw.Draw(im)

    # ── 카테고리 글래스 pill (이미지 위 좌상단, Liquid Glass) ──
    label = cat_en if cat_en == cat_ko else f"{cat_en} · {cat_ko}"
    im, _ = glass_pill(im, M, 44, label, font("bold", 28), fg=WHITE, dot=ac)
    d = ImageDraw.Draw(im)

    # ── 헤드라인 글래스 패널 (이미지 하단에 떠 있는 프로스티드 카드) ──
    tf = font("extrabold", 48)
    inner = W - 96 - 72                          # 패널 내부 텍스트 폭
    tlines = wrap(d, title, tf, inner)[:3]
    a, de = tf.getmetrics(); tlh = a + de + 8
    pad_t, pad_b = 36, 34
    panel_h = pad_t + len(tlines)*tlh + pad_b
    px0, px1 = 48, W-48
    py1 = BH + 38
    py0 = py1 - panel_h
    im = glass_layer(im, (px0, py0, px1, py1), radius=40, blur=22,
                     tint=(255, 255, 255, 158), border=(255, 255, 255, 190))
    d = ImageDraw.Draw(im)
    ty = py0 + pad_t
    for ln in tlines:
        d.text((px0+36, ty), ln, font=tf, fill=INK); ty += tlh

    # ── 본문 (크림 영역) ──
    y = py1 + 40
    d.rectangle([M, y, M+72, y+7], fill=ac); y += 32
    dl(d, M, y, body, font("regular", 36), INK2, W-2*M, 14, maxlines=4)

    # ── 출처 + 클릭 가능 링크 ──
    sy = H-176
    d.text((M, sy), "출처", font=font("bold", 26), fill=ac)
    d.text((M+72, sy), source, font=font("semibold", 30), fill=INK)
    ly = sy + 46
    disp = truncate(d, url.replace("https://", "").replace("http://", ""), font("medium", 26), W-2*M-40)
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

# ── 2026-06-15 (월) 브리핑 — 6/12(금)~6/15(월) 72시간 ─────────────────────
AI = [
 ("애플 WWDC 2026: Siri AI·iOS 27 전면 공개",
  "서핏·이오플래닛이 모두 주목한 이번 주 최대 AI 이슈. 애플이 WWDC 2026에서 iOS 27과 전면 재설계된 Siri AI를 공개했다. Gemini 연동·크로스앱 문맥 인식·원탭 비밀번호 업데이트 등 Apple Intelligence가 실사용 수준으로 도약했다는 평가다.",
  "TechCrunch", "https://techcrunch.com/2026/06/09/wwdc-2026-everything-announced-on-siri-ai-os-27-apple-intelligence-and-more/"),
 ("ChatGPT, 월 10억 명 돌파 — 역사상 가장 빠른 앱",
  "ChatGPT가 출시 약 3년 만에 월간 활성 사용자 10억 명을 달성했다. YouTube·TikTok·Instagram이 10억 명까지 5~8년 걸린 것과 비교해 압도적 속도다. 전 세계 AI 앱 경쟁에서 OpenAI가 1위 독주 구도를 굳히고 있어 마케터·디자이너의 AI 도입 압박도 빨라질 전망이다.",
  "The Next Web", "https://thenextweb.com/news/chatgpt-1-billion-monthly-active-users"),
 ("OpenAI, IPO 비밀 신고 — Anthropic과 나란히 상장 레이스",
  "OpenAI가 6월 8일 SEC에 비밀 IPO 신고서를 제출했다. 앞서 Anthropic도 IPO를 공식 신고하며 AI 양대 산맥이 동시에 공개시장을 향해 나아가고 있다. 상장 후 자금 조달로 인프라·연구 투자가 더욱 가속화될 것으로 보인다.",
  "TechCrunch", "https://techcrunch.com/2026/06/08/following-anthropic-openai-files-confidentially-for-ipo/"),
 ("Anthropic, Claude Fable 5 공개 — 추론·코딩 최고 성능",
  "Anthropic이 Claude Fable 5(코드명 Mythos)를 공개했다. 추론·수학·코딩 벤치마크에서 최상위 성능을 기록하며 GPT-4o를 일부 지표에서 앞섰다. 월 $100 Max 플랜 구독자에게 우선 제공되며, API는 별도 요금으로 접근 가능하다.",
  "CNBC", "https://www.cnbc.com/2026/06/09/anthropic-mythos-claude-fable-5.html"),
 ("OpenAI Codex, 직군별 AI 에이전트 6종 출시",
  "OpenAI가 Codex에 데이터 분석·크리에이티브 제작·영업·제품 디자인·투자 등 6개 직군 특화 플러그인을 추가했다. 주간 활성 사용자 500만 명으로 2월 대비 6배 성장. 화이트칼라 업무 자동화를 겨냥한 AI 에이전트 경쟁이 본격화됐다.",
  "TechCrunch", "https://techcrunch.com/2026/06/02/openai-launches-new-codex-tools-for-white-collar-work/"),
 ("마이크로소프트, 자체 AI 모델 공개 — OpenAI 의존 줄인다",
  "Microsoft가 자체 개발 AI 모델을 발표하며 OpenAI 의존도를 줄이겠다는 의지를 밝혔다. 소형 모델부터 대형 모델까지 라인업을 구축해 클라우드·엣지 비용을 낮추는 전략이다. AI 코딩·생산성 도구에서 OpenAI·Anthropic과 직접 경쟁 구도가 형성된다.",
  "CNBC", "https://www.cnbc.com/2026/06/02/microsoft-unveils-new-ai-models-lessen-reliance-on-openai-lower-costs.html"),
 ("Anthropic, IPO 공식 신고 — 기업가치 $9,650억",
  "Anthropic이 SEC에 상장 신고서를 제출하며 기업가치 약 9,650억 달러(약 1,300조 원)를 목표로 설정했다. 아마존·구글의 대규모 투자와 Claude 기반 B2B 서비스 성장이 뒷받침된 수치다. OpenAI IPO와 함께 AI 스타트업 역사상 최대 규모 상장이 될 전망이다.",
  "TechCrunch", "https://techcrunch.com/2026/06/01/anthropic-files-to-go-public/"),
]

DESIGN = [
 # ① 한국 핫이슈 — 투썸 한글 자모 리브랜딩 (호불호 화제)
 ("투썸플레이스, 한글 자모로 새 심벌 리브랜딩 — SNS 화제",
  "투썸플레이스가 6월 10일 한글 자모를 모티프로 한 새 심벌로 BI를 리뉴얼했다. 공개 직후 SNS에서 호불호가 크게 갈렸고, 이틀 만에 일부 디자인을 수정하는 등 논란이 이어졌다. 국내 브랜드 리브랜딩에서 '한국적 정체성'과 대중 수용성 사이의 균형이 핵심 화두임을 보여준 사례다.",
  "디자인 나침반", "https://designcompass.org/2026/06/10/twosome-place-korean-symbol-rebranding/"),
 # ② 애플 리퀴드 글래스 — 글래스모피즘 OS 표준화 (디바이스/UI)
 ("애플 '리퀴드 글래스', iOS 27서 전면 정교화",
  "애플이 WWDC 2026에서 작년 도입한 '리퀴드 글래스' 디자인 언어를 대폭 다듬었다. 컨트롤 센터에 투명도 슬라이더를 추가해 가독성 논란을 해소하고, 앱 실행 30%·사진 로딩 70% 속도 개선을 더했다. 글래스모피즘이 OS 표준 UI로 자리 잡으며 디자이너의 대응이 필수가 됐다.",
  "Cult of Mac", "https://www.cultofmac.com/news/liquid-glass-changes-ios-27-macos-27"),
 # ③ 대한항공 리브랜드 (브랜드 아이덴티티)
 ("대한항공 + Lippincott, 40년 만의 리브랜드",
  "대한항공이 글로벌 브랜딩 에이전시 Lippincott과 협력해 40년 만에 전면 브랜드 리뉴얼을 단행했다. 태극 모티프를 현대적으로 재해석한 새 로고와 럭셔리 컬러 팔레트가 적용됐다. 아시아나 합병을 앞두고 글로벌 프리미엄 항공사 포지셔닝을 공고히 하려는 전략이 담겼다.",
  "Creative Boom", "https://www.creativeboom.com/news/korean-air-unveils-elegant-new-brand-identity-in-collaboration-with-lippincott/"),
 # ④ Adobe 아이덴티티
 ("Adobe, Mother Design과 글로벌 브랜드 아이덴티티 재정립",
  "Adobe가 글로벌 크리에이티브 에이전시 Mother Design과 함께 브랜드 아이덴티티를 전면 개편했다. AI 기반 창작 플랫폼으로 진화한 Adobe의 새 비전을 시각적으로 구현했으며, 다이나믹한 타입 시스템과 스펙트럼 컬러 팔레트가 특징이다.",
  "Creative Boom", "https://www.creativeboom.com/news/reshaping-adobes-global-brand-identity-with-mother-design/"),
 # ⑤ LG 리브랜드
 ("LG 전자 + Wolff Olins, 'Life's Good' 브랜드 리뉴얼",
  "LG 전자가 Wolff Olins와 협업해 상징적인 슬로건 'Life's Good'을 중심으로 브랜드 아이덴티티를 새롭게 정의했다. 기술 기업에서 라이프스타일 기업으로의 전환을 시각 언어로 풀어냈다는 평가다. 글로벌 캠페인과 연동한 디자인 시스템이 함께 공개됐다.",
  "Creative Boom", "https://www.creativeboom.com/news/lg-electronics-kicks-off-its-lifes-good-campaign-with-renewed-brand-identity/"),
 # ⑥ 가구 — 이케아 PS 2026 (공기주입식 의자)
 ("이케아 PS 2026: 공기주입식 의자 등 가구 44종 공개",
  "이케아가 밀라노 디자인위크에서 PS 2026 컬렉션 44종을 공개했다. 미카엘 악셀손이 20개 시제품 끝에 완성한 공기주입식 1인용 의자, 회전 플로어 램프 등 '놀이 같은 기능성'이 핵심이다. 평팩·발펌프 동봉으로 합리적 가격과 지속가능성을 동시에 잡았다.",
  "Dezeen", "https://www.dezeen.com/2026/05/13/ikea-ps-collection-furniture/"),
 # ⑦ 가구/프로덕트 — 코펜하겐 3 Days of Design 신제품
 ("코펜하겐 '3 Days of Design 2026' 신제품 8선",
  "400여 브랜드가 모인 코펜하겐 디자인위크(6/10~12)에서 주목할 신제품 8종이 공개됐다. 단일 알루미늄 판을 접어 만든 톰 페레데이의 'Sail' 테이블 등 소재 실험과 단순한 형태가 돋보였다. 북유럽 가구·오브제 트렌드의 최전선을 보여준다.",
  "Dezeen", "https://www.dezeen.com/2026/06/12/products-tiles-furniture-3-days-of-design-2026/"),
 # ⑧ 한국 프로덕트 — ILKW 조명
 ("한국 조명 브랜드 ILKW '스노우맨22' 컬렉션",
  "한국 조명 브랜드 ILKW가 손으로 분 유리에 눈사람 형태를 결합한 '스노우맨22' 컬렉션을 선보였다. 벽·플로어·테이블·펜던트로 확장되는 장난기 어린 유리 셰이드가 특징이다. 국내 디자인 브랜드가 글로벌 디자인 매체에 정식 소개된 반가운 사례다.",
  "Dezeen", "https://www.dezeen.com/2026/06/01/snowman22-lighting-lkw-dezeen-showroom/"),
 # ⑨ 한국 인테리어/공간 1 — 성수동 팝업
 ("성수동 6월 팝업스토어 공간 디자인 총정리",
  "2026년 6월 성수동에서 운영 중인 주요 팝업스토어 공간이 한자리에 정리됐다. 뉴발란스 'Run Hub' 러닝 허브 등 브랜드 서사를 공간 경험으로 풀어낸 사례가 늘고 있다. 리테일·공간 기획자가 참고할 최신 팝업 인테리어 레퍼런스다.",
  "팝가 Popga", "https://popga.co.kr/content/magazine/284"),
 # ⑩ 한국 인테리어/공간 2 — 팝업 공간 디자인 전략
 ("Z세대를 부르는 팝업스토어 공간 디자인 전략",
  "팝업스토어가 단순 판매를 넘어 브랜드 인지·경험의 무대로 진화하면서, 공간에 브랜드 내러티브를 담는 설계가 핵심 전략으로 떠올랐다. 사례 중심으로 Z세대를 모으는 공간 디자인 문법을 짚었다. 국내 리테일 마케터·공간 디자이너에게 실전 인사이트를 준다.",
  "오픈애즈 OpenAds", "https://openads.co.kr/content/contentDetail?contsId=17396"),
 # ⑪ 프로덕트/UX — Figma 디자이너 수요 리포트
 ("Figma 2026 리포트: 디자이너 수요 왜 다시 급증하나",
  "Figma가 발표한 2026 보고서에 따르면 AI 툴 확산에도 불구하고 디자이너 채용 수요가 오히려 증가했다. AI가 반복 작업을 대체하는 대신, 전략적 디자인 씽킹과 시스템 설계 역량에 대한 프리미엄이 높아진 것으로 분석된다.",
  "Figma Blog", "https://www.figma.com/blog/why-demand-for-designers-is-on-the-rise/"),
 # ⑫ 디자인 혁신 — Cannes Innovation Lions
 ("칸 라이언즈 2026 Innovation Lions 쇼트리스트 공개",
  "칸 라이언즈 2026 Innovation Lions 쇼트리스트가 발표됐다. AI 기반 크리에이티브 도구, 인터랙티브 경험 설계, 지속가능 디자인 솔루션이 주요 수상 후보군을 구성했다. 실무 디자이너·마케터가 올해 혁신의 방향성을 가늠할 바로미터다.",
  "Roast Brief", "https://roastbrief.us/cannes-lions-2026-innovation-lions-shortlist-announced/"),
]

MARKETING = [
 ("칸 라이언즈 2026 Titanium 후보 18캠페인 공개",
  "칸 라이언즈 2026 Titanium Lions 쇼트리스트 18개 캠페인이 발표됐다. IKEA·Heineken·Asics·Oreo 등 글로벌 브랜드의 문화적 긴장감을 활용한 캠페인들이 포함됐다. Titanium은 업계 방향을 바꾸는 혁신적 작업에 수여되는 최고 권위 부문이다.",
  "Adweek", "https://www.adweek.com/creativity/these-18-campaigns-are-competing-for-the-coveted-cannes-titanium-lion/"),
 ("칸 라이언즈 Glass: 변화를 위한 라이언 쇼트리스트",
  "사회 변화를 이끄는 마케팅을 선정하는 Glass: Lion for Change 쇼트리스트가 공개됐다. 젠더 편견 해소·기후 행동·포용성을 주제로 한 글로벌 캠페인들이 이름을 올렸다. 브랜드가 사회적 목소리를 내는 방식이 수상의 핵심 기준으로 자리 잡고 있다.",
  "Marketing Report", "https://marketingreport.one/creation/cannes-lions-releases-first-2026-award-shortlists.html"),
 ("Omnicom + Google, YouTube 라이브스트림 파트너십 체결",
  "Omnicom이 칸 라이언즈 현장에서 Google과 YouTube 라이브스트림 광고 파트너십을 발표했다. 실시간 이벤트 맥락과 연동된 타기팅 광고로 브랜드 안전성과 도달률을 동시에 높이는 솔루션이다. 라이브 콘텐츠 광고 시장에서 구글의 영향력이 더욱 확대될 전망이다.",
  "Digiday", "https://digiday.com/media-buying/omnicom-wraps-up-its-cannes-lions-presence-with-a-youtube-livestream-partnership/"),
 ("Omnicom, Disney·Walmart과 인플루언서 라이브 커머스 협약",
  "Omnicom이 칸 현장에서 Disney와 Walmart를 대상으로 인플루언서 기반 라이브 커머스 파트너십을 동시 체결했다. 크리에이터 경제와 대형 리테일러를 잇는 새 광고 모델로, 인플루언서 마케팅이 퍼포먼스 채널로 진화하는 흐름을 반영한다.",
  "Digiday", "https://digiday.com/media-buying/omnicom-strikes-partnerships-at-cannes-lions-with-disney-and-walmart-around-harnessing-live/"),
 ("VML, 칸 라이언즈 Innovation·Titanium 동시 쇼트리스트",
  "글로벌 크리에이티브 에이전시 VML이 칸 라이언즈 2026에서 Innovation과 Titanium 두 부문 쇼트리스트에 동시 진입했다. AI 기반 크리에이티브 솔루션과 사회적 임팩트 캠페인 두 축에서 모두 인정받은 것으로, 에이전시 혁신 역량의 지표로 주목된다.",
  "VML", "https://www.vml.com/news/vml-shortlisted-for-innovation-and-titanium-honors-at-cannes-lions-2026"),
 ("칸 라이언즈 2026 완벽 프로그램 가이드 (6/22~26)",
  "칸 라이언즈 2026이 6월 22~26일 프랑스 칸에서 열린다. 주요 세미나·시상 일정과 주목할 세션 큐레이션이 정리됐다. 크리에이티브 업계 종사자라면 올해 수상 트렌드를 미리 파악해 두면 기획·제안에 직접 활용할 수 있다.",
  "Famous Campaigns", "https://www.famouscampaigns.com/2026/06/cannes-lions-2026-your-essential-programme-guide/"),
 ("2026 마케팅 트렌드 전망 — 브랜드가 준비할 것들",
  "Marketing Dive가 정리한 2026 마케팅 트렌드 보고서. AI 자동화·퍼스트파티 데이터·크리에이터 파트너십·지속가능 메시지가 핵심 축으로 제시됐다. 브랜드가 단기 성과보다 신뢰 구축과 장기 관계에 집중해야 한다는 것이 공통 시사점이다.",
  "Marketing Dive", "https://www.marketingdive.com/news/marketing-trends-outlook-2026/810740/"),
]

# (영문 라벨, 한글 라벨, 액센트, 파일 접미사, 기사 리스트)
SECTIONS = [
 ("AI", "AI", VIOLET, "ai", AI),
 ("DESIGN", "디자인", BLUE, "design", DESIGN),
 ("MARKETING", "마케팅", CORAL, "marketing", MARKETING),
]

# ---------------------------------------------------------------- 링크 검증 (죽은 링크/404 차단)
def validate_url(url):
    """렌더 시점(GitHub Actions=인터넷)에서 URL이 실제로 열리는지 확인.
    HEAD 거부(405)·일부 봇 차단 사이트는 GET으로 재확인. 4xx/5xx·연결 실패면 False."""
    for method in ("head", "get"):
        try:
            r = getattr(requests, method)(url, headers=UA, timeout=12,
                                          allow_redirects=True, stream=(method == "get"))
            code = r.status_code
            if method == "get":
                r.close()
            if code < 400:
                return True
            if code in (403, 405, 406) and method == "head":
                continue   # HEAD 차단 가능성 → GET 재시도
            if code >= 400:
                return False
        except Exception:
            continue
    return False

def safe_link(url):
    """url이 죽었으면(404 등) 404가 날 수 없는 해당 매체 도메인 루트로 폴백.
    → 사용자가 어떤 카드에서도 죽은 링크를 만나지 않게 보장한다."""
    if validate_url(url):
        return url
    from urllib.parse import urlparse
    p = urlparse(url)
    root = f"{p.scheme}://{p.netloc}/"
    print(f"  ⚠ 죽은 링크 감지 → 도메인 루트로 폴백: {url} → {root}")
    return root if validate_url(root) else url

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
            forced = _force(u)            # og:image는 원본 URL로 시도(이미지 관련성 보존)
            link = safe_link(u)           # 클릭 링크만 검증 — 죽었으면 안전 폴백
            pages.append(card(idx, total, cat_en, cat_ko, ac, t, b, s, link,
                              f"{idx:02d}_{suffix}.png", force_search=forced,
                              img_url=u))
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
