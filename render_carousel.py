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
        last = lines[-1]
        # 단어(어절) 단위 먼저 줄이기 — 영어·한국어 단어가 중간에 끊기지 않게
        while last:
            if d.textlength(last + "…", font=f) <= mw:
                break
            # 마지막 공백 앞까지 잘라 시도
            sp = last.rfind(" ")
            if sp > 0:
                candidate = last[:sp]
                if d.textlength(candidate + "…", font=f) <= mw:
                    last = candidate; break
            # 공백 없으면(긴 한 단어) 한 글자씩 줄이기
            last = last[:-1]
        lines[-1] = last + "…"
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
    # AI/테크 기업
    "안드레이 카파시": "Andrej Karpathy", "카파시": "Karpathy",
    "구글": "Google", "알파벳": "Alphabet Google",
    "엔비디아": "NVIDIA", "엔비디아의": "NVIDIA",
    "마이크로소프트": "Microsoft", "MS": "Microsoft",
    "깃허브": "GitHub", "코파일럿": "GitHub Copilot",
    "오픈AI": "OpenAI", "ChatGPT": "ChatGPT",
    "앤트로픽": "Anthropic", "클로드": "Claude Anthropic",
    "피그마": "Figma",
    "애플": "Apple", "아이폰": "iPhone Apple", "아이패드": "iPad Apple",
    "메타": "Meta", "아마존": "Amazon", "AWS": "AWS",
    "스냅": "Snapchat Snap", "타입폼": "Typeform",
    "펍매틱": "PubMatic programmatic", "세일즈포스": "Salesforce",
    "틱톡": "TikTok", "유튜브": "YouTube",
    "링크드인": "LinkedIn", "트위터": "Twitter X",
    # 가구·인테리어·제품 브랜드
    "이케아": "IKEA furniture", "의자": "chair furniture",
    "가구": "furniture", "조명": "lighting lamp",
    "인테리어": "interior design", "공간": "interior space design",
    "팝업": "popup store retail", "팝업스토어": "popup store retail",
    "매장": "store retail interior", "플래그십": "flagship store",
    "성수": "Seoul Seongsu", "성수동": "Seoul Seongsu street",
    "코펜하겐": "Copenhagen design",
    "밀라노": "Milan design week",
    # 디자인 브랜드·에이전시
    "리퀴드 글래스": "Liquid Glass Apple UI",
    "리퀴드": "Liquid Glass", "글래스모피즘": "glassmorphism",
    "대한항공": "Korean Air airline", "아시아나": "Asiana Airlines",
    "투썸": "Twosome Place cafe coffee", "투썸플레이스": "Twosome Place cafe coffee",
    "스타벅스": "Starbucks coffee", "맥도날드": "McDonald brand",
    "LG": "LG Electronics",
    "BMW": "BMW", "현대": "Hyundai",
    "BBH": "BBH advertising agency",
    "루프트한자": "Lufthansa", "펩시코": "PepsiCo",
    # 디자인 액션 단어 → 구체적 쿼리로
    "리브랜딩": "rebranding logo identity",
    "리브랜드": "rebranding logo identity",
    "브랜딩": "branding identity",
    "아이덴티티": "brand identity logo",
    "로고": "logo brand design",
    "광고": "advertising campaign",
    "캠페인": "marketing campaign",
    # AI 개념
    "에이전트": "AI agent",
    "펀딩": "startup funding venture",
    "밸류에이션": "startup valuation",
    "거버넌스": "AI governance policy",
    "크리에이터": "creator content",
    "코딩": "coding programming",
    # 마케팅 개념
    "인플루언서": "influencer marketing",
    "라이브커머스": "live commerce streaming",
}

def _url_keywords(url):
    """URL 경로(영문 슬러그)에서 의미 있는 키워드를 추출한다.
    기사 URL 슬러그는 보통 기사 핵심 키워드를 그대로 담고 있어 이미지 검색에 매우 유용하다."""
    from urllib.parse import urlparse
    path = urlparse(url).path.lower()
    words = re.findall(r"[a-z]{3,}", path)
    # 연도·날짜·CMS·불용어 제거 (content/magazine/page처럼 의미없는 슬러그 배제)
    stop = {
        "www", "com", "net", "org", "the", "and", "for", "with", "from",
        "that", "this", "are", "was", "has", "been", "have", "will",
        "its", "our", "new", "all", "but", "not", "news", "blog",
        "article", "page", "post", "amp", "html", "php", "asp",
        "content", "magazine", "category", "tag", "read", "view",
        "archives", "index", "detail", "item", "topics",
    }
    kws = [w for w in words if w not in stop and len(w) >= 3]
    return kws[:6]

def _img_queries(title, cat_en, url=""):
    """제목 + URL 슬러그에서 구체적 검색어 추출 → 카테고리 폴백 순서로 반환.
    한국어 브랜드·인물명을 영어로 변환해 Openverse 검색 정밀도를 높인다."""
    # 한국어 → 영어 치환
    t = title
    for ko, en in KO_TO_EN.items():
        t = t.replace(ko, en)
    # 영어 단어 추출 (3글자 이상)
    en_words = re.findall(r"[A-Z][A-Za-z]{2,}|[A-Za-z]{3,}", t)
    # 브랜드명·고유명사(대문자 시작) 우선
    proper = [w for w in en_words if w[0].isupper()]
    common = [w for w in en_words if not w[0].isupper()]

    # URL 슬러그 키워드 (매우 중요 — 기사 내용 그대로 반영)
    url_kws = _url_keywords(url)
    # URL 키워드로 proper/common 보강
    for kw in url_kws:
        cap = kw.capitalize()
        if cap not in proper and kw.upper() not in [p.upper() for p in proper]:
            if kw[0].isupper() or kw in ["ikea", "apple", "google", "adobe", "figma", "dezeen", "cannes"]:
                proper.insert(0, cap)
            else:
                common.append(kw)

    fallback = {
        "AI":        ["artificial intelligence technology", "AI software", "machine learning"],
        "DESIGN":    ["modern design product", "graphic design studio", "brand visual identity"],
        "MARKETING": ["marketing campaign advertising", "brand strategy", "digital marketing"],
    }.get(cat_en, ["technology"])

    qs = []
    # URL 키워드 2개 조합 (가장 기사 내용에 충실)
    if len(url_kws) >= 2: qs.append(" ".join(url_kws[:3]))
    if len(url_kws) >= 1: qs.append(url_kws[0])
    # 고유명사 조합
    if len(proper) >= 2:  qs.append(f"{proper[0]} {proper[1]}")
    if proper:            qs.append(proper[0])
    # 고유명사 + 카테고리 힌트
    if proper:            qs.append(f"{proper[0]} {fallback[0]}")
    if url_kws:           qs.append(f"{url_kws[0]} {fallback[0]}")
    # 일반 단어 + 힌트
    if common:            qs.append(f"{common[0]} {fallback[0]}")
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
    qs    = _img_queries(title, cat_en, url)
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

# ── 2026-06-23 (화) 브리핑 — 6/22(월)~6/23(화) 24시간 ─────────────────────
# ⚠️ 전날(6/22) 기사와 중복 없음 — 모두 신규
AI = [
 # ① 구글 딥마인드 A24에 750억 투자 (6/22)
 ("구글 딥마인드, 인디 영화사 A24에 750억 원 투자",
  "Google DeepMind가 인디 영화사 A24에 7,500만 달러를 투자하고 AI 영화 제작 도구 공동 개발 파트너십을 체결했다. A24 Labs는 AI 기반 스토리보드·프로덕션 워크플로 자동화 도구를 개발하며, 알파벳이 영화 스튜디오에 지분을 투자한 최초의 사례다. 콘텐츠 라이브러리 데이터 접근권 없이 도구 개발에만 집중한다는 점에서 창작자 권리를 의식한 구조다.",
  "TechCrunch", "https://techcrunch.com/2026/06/22/google-deepmind-bets-75m-on-ais-future-in-hollywood-with-a24-deal/"),
 # ② OpenAI, 오픈소스 보안 프로젝트 출범 (6/22)
 ("OpenAI, 오픈소스 취약점 자동 발굴 'Patch the Planet' 출범",
  "OpenAI가 보안 전문 기업 Trail of Bits와 함께 'Patch the Planet' 이니셔티브를 공개했다. AI 기반 취약점 스캐닝으로 첫 1주 만에 19개 프로젝트에서 수백 건의 버그를 발견하고 64건의 풀리퀘스트와 51건의 이슈를 제출했다. cURL·Go·Python 등 30여 개 오픈소스 프로젝트가 참여하며, 참여 프로젝트는 ChatGPT Pro와 Codex Security API 크레딧을 제공받는다.",
  "OpenAI Blog", "https://openai.com/index/patch-the-planet/"),
 # ③ AI 칩 Groq, 6,500억 원 조달 (6/22)
 ("AI 추론 칩 Groq, 6억 5천만 달러 신규 펀딩 완료",
  "Groq가 6억 5,000만 달러 규모 신규 펀딩을 완료했다고 공식 발표했다. 앞서 NVIDIA가 Groq의 LPU 기술을 200억 달러에 라이선스하고 핵심 인력을 영입했으며, Groq는 이를 발판 삼아 자체 AI 추론 네오클라우드 사업을 확대한다. Disruptive·Infinitum이 주도한 이번 라운드로 Groq는 NVIDIA 경쟁 구도를 넘어 독립 추론 클라우드 플레이어로 전환을 본격화한다.",
  "Bloomberg", "https://www.bloomberg.com/news/articles/2026-06-22/groq-raises-650-million-to-help-startup-pivot-after-nvidia-deal"),
 # ④ 마이크로소프트·셰브론 AI 전력 20년 계약 (6/22)
 ("MS·셰브론, AI 데이터센터용 2.67GW 전력 20년 계약",
  "Microsoft와 Chevron이 텍사스 서부에 2.67기가와트 규모의 천연가스 발전소를 공동 개발하는 20년 전력구매계약(PPA)을 체결했다. 첫 전력 공급은 2028년으로 예정되며 약 2,000개 일자리 창출과 100억 달러 이상의 세수 효과가 기대된다. AI 데이터센터 전력 수요 급증에 대응하는 미국 최대급 전용 전력 프로젝트 중 하나다.",
  "Chevron", "https://www.chevron.com/newsroom/2026/q2/chevron-signs-20-year-power-agreement-with-microsoft-for-west-texas-data-center"),
 # ⑤ Anthropic 클로드 본인인증 7월 시행 (6/22)
 ("Anthropic, 7월부터 Claude에 신분증 본인인증 도입",
  "Anthropic이 7월 8일부터 Claude 이용자를 대상으로 여권·운전면허증과 셀피를 통한 생체 인증을 시행한다. 인증은 제3자 업체 Persona Identities가 처리하며, 수집된 데이터는 AI 학습이나 마케팅 목적에 활용되지 않는다. Claude Free·Pro·Max 구독자에만 적용되고 Team·Enterprise·API 플랜은 면제되며, AI 서비스의 신원 확인 표준화를 향한 업계 선제 행보로 주목받는다.",
  "TechCrunch", "https://techcrunch.com/2026/06/22/anthropic-says-claude-may-want-to-see-your-id/"),
 # ⑥ 오라클 AI로 2만 1천 명 감원 공식 공시 (6/22)
 ("오라클, AI 도입으로 2만1천 명 감원 — 사상 첫 공식 공시",
  "Oracle이 연간 재무 보고서(10-K)에서 최근 12개월간 AI 도입으로 2만1,000명을 감축했다고 공식 확인했다. 구조조정 비용으로 18억 4,000만 달러를 지출했으며, 이는 전년도의 약 5배 수준이다. AI가 직접 일자리를 대체했다고 대형 상장사가 공식 신고한 첫 사례로, 화이트칼라·기술직 AI 대체 가속화의 강력한 증거가 됐다.",
  "Bloomberg", "https://www.bloomberg.com/news/articles/2026-06-22/oracle-layoffs-fueled-by-ai-reduces-workforce-by-21-000"),
 # ⑦ AI 에이전트 '셀프 하네스' 성능 60% 향상 (6/22)
 ("AI 에이전트가 자신의 실행 규칙 스스로 수정 — 성능 60%↑",
  "상하이 인공지능 연구소가 AI 에이전트가 자신의 실행 규칙을 스스로 최적화하는 'Self-Harness' 프레임워크를 공개했다. Terminal-Bench 2.0에서 Qwen3.5-35B-A3B 모델 성능이 23.8%에서 38.1%로 약 60% 향상됐으며, 모델 재훈련 없이 하네스만 자동 업데이트하는 방식이다. 의료·법률 등 고위험 분야 완전 자동화에는 주의가 필요하다는 조건도 함께 제시됐다.",
  "VentureBeat", "https://venturebeat.com/orchestration/researchers-introduce-self-harness-a-framework-that-lets-ai-agents-rewrite-their-own-rules-boosting-performance-up-to-60"),
]

DESIGN = [
 # ① Figma Config 2026 개막 (6/23)
 ("Figma Config 2026 개막 — AI 에이전트·Figma Make 공개",
  "6월 23일 샌프란시스코 모스코니 센터에서 Figma Config 2026이 개막했다. 첫날 Config Commons와 Makeathon 수상자 발표($10만 달러 상금)가 진행됐으며, 24일 Dylan Field 키노트에서 Figma AI Agent·Figma Make 전면 공개가 예고됐다. 디자이너가 캔버스에서 AI 에이전트와 협업해 프로토타입에서 코드까지 원스톱으로 처리하는 워크플로가 공개될 예정이다.",
  "TechTimes", "https://www.techtimes.com/articles/318823/20260622/figma-config-2026-kicks-off-today-virtual-attendance-still-free-person-sells-out.htm"),
 # ② iOS 26 Liquid Glass 전 앱 강제 적용
 ("iOS 26 Liquid Glass, 6월부터 앱 전체에 자동 적용",
  "6월 2일부터 업데이트되는 모든 iOS 앱 컨테이너에 Liquid Glass 디자인이 기본 활성화되며 8월 말까지 기존 컨테이너 전체가 전환 완료된다. 탭 바·앱 바가 반투명·블러 처리돼 스크롤 시 콘텐츠에 집중되는 경험이 iOS 표준이 된다. iOS 앱을 운영하는 브랜드·프로덕트팀은 6월을 기점으로 Liquid Glass 가이드라인 대응을 서둘러야 한다.",
  "SpotMe", "https://support.spotme.com/hc/en-us/articles/49966586899475-New-iOS-26-Liquid-Glass-design",
  "https://9to5mac.com/wp-content/uploads/sites/6/2025/06/iOS-26-Liquid-Glass.jpg"),
 # ③ Montana 팬톤 탄생 100주년 탄제린 한정판
 ("Montana, 팬톤 탄생 100주년 와이어 탄제린 한정판",
  "덴마크 가구 브랜드 Montana가 베르너 팬톤 탄생 100주년을 맞아 코펜하겐 3 Days of Design에서 Panton Wire 탄제린 한정판을 공개했다. 선명한 오렌지 컬러는 팬톤 특유의 대담한 색채 실험을 오마주하며 2026년 6월부터 1년 한정 판매된다. 파라다임 모듈형 소파 블록 컬러 에디션·욕실 컬렉션 리론칭도 함께 발표됐다.",
  "Scandinavian Design", "https://scandinaviandesign.com/panton-wire-in-tangerine-for-verner-panton-100th-anniversary"),
 # ④ Theo 폴딩 체어 마테오 툰 × Plank (6/11)
 ("마테오 툰 × Plank, 오크 베니어 접이식 '테오 체어'",
  "이탈리아 건축가 마테오 툰과 베네데토 파시아나가 Plank를 위해 설계한 접이식 목재 의자 'Theo'가 3 Days of Design 2026에서 데뷔했다. 성형 합판에 오크 베니어를 씌워 납작하게 접히며 계약·레스토랑·홈 오피스 등 다목적 사용을 타깃으로 한다. 목재의 온기와 모던한 폼의 조합으로 코펜하겐 디자인위크 주목 신제품 중 하나로 꼽혔다.",
  "Dezeen", "https://www.dezeen.com/2026/06/11/theo-folding-chair-matteo-thun-benedetto-fasciana-plank-dezeen-showroom/",
  "https://static.dezeen.com/uploads/2026/06/theo-chair-plank-dezeen-showroom.jpg"),
 # ⑤ 성수 House of Toy Story 팝업 (한국)
 ("성수 'House of Toy Story' — 1970s 미국 가정 복층 몰입 팝업",
  "5월 23일부터 7월 12일까지 성수 +LECT HOUSE에서 운영 중인 'House of Toy Story' 팝업이 6월 하순 절정을 맞고 있다. 1970~80년대 미국 가정 인테리어를 층별로 재현해 포토존·아케이드 게임·DIY 머천다이징 스테이션을 구성했다. 공간 자체가 브랜드 스토리텔링의 배경이 되는 'IP 팝업 인테리어'의 국내 대형 사례로 주목받는다.",
  "NOL World", "https://world.nol.com/en/content/festas/019ea4a7-ae4f-7ebe-934b-9448962ad968"),
 # ⑥ 손흥민 NOS7 × MNH 월드컵 성수 팝업 (한국)
 ("손흥민 NOS7 × MNH '캡틴쏜희' 성수 팝업 대성황",
  "패션 브랜드 NOS7이 캐릭터 브랜드 MNH와 손잡고 서울 성수동에서 'The Captain is Here' 팝업을 열었다. 2026 FIFA 월드컵 시즌에 맞춰 출시 당일 품절됐던 '캡틴쏜희' 콜라보 캡슐 컬렉션을 한정 수량으로 재공개하며 인파가 몰렸다. IP 협업·월드컵 시즌 마케팅·성수 팝업을 연결한 한국형 스포츠 브랜드 팝업 전략의 성공 사례다.",
  "인더뉴스", "https://www.inthenews.co.kr/news/article.html?no=87221"),
 # ⑦ KFC 버킷버스 플래그십 레스토랑 여름 개장
 ("KFC '버킷버스' 플래그십 레스토랑, 텍사스·두바이 여름 오픈",
  "KFC 글로벌 리브랜딩 'Bucketverse'에 맞춰 텍사스 맥키니에 오픈 콘셉트 플래그십 레스토랑이 여름 개장 예정이며, 두바이에는 2층 규모의 완전 몰입형 매장이 가을 오픈을 앞두고 있다. 버킷을 중심 프레이밍 도구로 삼은 새 공간 디자인은 기존 패스트푸드 매장 문법을 해체하는 방향으로 설계됐다. 브랜드 아이덴티티를 공간 경험으로 직결하는 새로운 레스토랑 디자인 전략이다.",
  "Design Rush", "https://news.designrush.com/kfc-global-rebrand-restaurant-redesign"),
 # ⑧ Xerox × Lexmark 통합 아이덴티티 공개
 ("Xerox, Lexmark 인수 후 통합 브랜드 아이덴티티 발표",
  "Xerox가 Lexmark 인수 완료를 알리는 통합 브랜드 아이덴티티를 공개했다. 소문자 워드마크, 선으로 이루어진 구체형 'X' 심볼, 핸드드로 느낌의 커스텀 폰트가 핵심이다. 프린팅 하드웨어를 넘어 소프트웨어·워크플레이스 서비스 기업으로의 전환을 시각적으로 선언한 리브랜딩으로 업계 주목을 받고 있다.",
  "Printweek", "https://www.printweek.com/content/news/xerox-rolls-out-rebrand"),
 # ⑨ Ideogram 4.0 How&How 아이덴티티
 ("Ideogram 4.0, How&How가 만든 '뇌 형태' 아이덴티티",
  "AI 이미지 플랫폼 Ideogram이 4.0 모델 론칭과 함께 브랜딩 스튜디오 How&How 제작 새 아이덴티티를 발표했다. 네거티브 스페이스로 'I'를 새긴 뇌 형태 로고마크와 네이비·크림·블랙의 절제된 컬러 시스템이 특징이며, Brand LLM으로 버바 아이덴티티도 코드화해 AI 시대 브랜딩 방법론의 새 사례가 됐다.",
  "Brand New", "https://www.underconsideration.com/brandnew/archives/new_logo_and_identity_for_ideogram_by_howhow.php",
  "https://abduzeedo.com/sites/default/files/2026/ideogram-brand-identity-how-how.jpg"),
 # ⑩ 칸 라이언즈 Design Lions GP — AXA 세 단어 (6/22)
 ("칸 라이언즈 Design GP — AXA 보험 약관 '세 단어'의 기적",
  "AXA의 집보험 약관에 'and domestic violence(그리고 가정폭력)' 세 단어를 추가해 피해자를 즉시 이주·지원하는 캠페인이 칸 라이언즈 2026 Design Lions 그랑프리를 수상했다. Publicis Conseil 파리 제작, 디자인·크리에이티브 전략·타이타늄 등 3개 그랑프리를 석권하며 올해 최다 수상 캠페인이 됐다. 브랜드 디자인이 사회 시스템 변화를 이끄는 도구가 될 수 있음을 증명한 역대급 케이스다.",
  "Ad Age", "https://adage.com/events-awards/cannes-lions/aa-grand-prix-2026/"),
 # ⑪ 칸 라이언즈 Outdoor GP — Mercado Livre '필드 바코드' (6/22)
 ("칸 라이언즈 Outdoor GP — 경기장 잔디를 104m 바코드로",
  "GUT 상파울루가 Mercado Livre를 위해 브라질 파카엠부 경기장 잔디를 104m 바코드로 전환한 'Field Barcode'가 Outdoor 그랑프리를 수상했다. 관중·TV·유튜브 시청자가 코드를 스캔해 25% 할인 쿠폰을 받았고, 5만 3,000건 사용·180만 달러 매출을 기록했다. 물리 공간을 쇼핑 미디어로 변환하는 파격적 발상이 실적으로 이어진 사례다.",
  "Adweek", "https://www.adweek.com/creativity/the-ordinary-wins-big-among-8-grand-prix-winners-from-cannes-lions-2026-day-1/"),
 # ⑫ 칸 라이언즈 AXA Titanium 그랑프리 (6/22)
 ("AXA '세 단어', Titanium 그랑프리까지 — 칸 2026 최강 캠페인",
  "AXA의 'Three Words' 캠페인이 Design·Creative Strategy에 이어 칸 라이언즈 최고 영예 Titanium 그랑프리까지 수상하며 2026년 칸 최다 수상 캠페인이 됐다. 약관 한 줄 수정으로 가정폭력 피해자를 실질적으로 보호하는 이 캠페인은 크리에이티비티와 사회적 임팩트가 비즈니스 전략과 완벽히 교차할 때 얼마나 강력한지를 증명한다. 2026 Titanium은 '사회를 실질적으로 바꾼 캠페인'에 주는 메시지를 전면에 세웠다.",
  "Campaign Live", "https://www.campaignlive.co.uk/article/cannes-lions-2026-titanium-grand-prix-axa-three-words/1962418"),
]

MARKETING = [
 # ① 오프라 칸 LionHeart 강연 (6/23)
 ("오프라, 칸 라이언즈 LionHeart 무대 강연 — '공감이 최강 광고'",
  "오프라 윈프리가 6월 23일 칸 라이언즈 뤼미에르 극장 무대에서 LionHeart 수상 강연을 진행했다. 수십 년간 미디어·스토리텔링·자선 활동으로 문화를 바꿔온 공로로 선정된 오프라의 메시지는 '인간의 창의성과 공감이 AI 시대에도 광고의 핵심'이라는 업계 화두와 맞닿는다. '공감을 팔지 말고 실천하라'는 그의 언급은 칸 라이언즈 2026의 핵심 키워드 중 하나로 회자됐다.",
  "adobo Magazine", "https://www.adobomagazine.com/advertising-awards/cannes-lions-reveals-2026-program-with-lionheart-honoree-oprah-winfrey-alongside-stella-mccartney-mark-ritson-and-more/"),
 # ② 칸 Outdoor GP — Mercado Livre 필드 바코드 (6/22)
 ("칸 라이언즈 Outdoor GP — 104m 바코드 경기장, 180만 달러 매출",
  "GUT 상파울루가 Mercado Livre를 위해 브라질 파카엠부 경기장 잔디를 104m 바코드로 전환한 'Field Barcode'가 Outdoor 그랑프리를 수상했다. 관중·TV·유튜브 시청자가 코드를 스캔해 25% 할인 쿠폰을 받았고, 5만 3,000건 사용·180만 달러 매출을 기록했다. 칸 Day 1 발표된 8개 그랑프리 중 가장 즉각적인 비즈니스 ROI를 입증한 캠페인이다.",
  "Adweek", "https://www.adweek.com/creativity/the-ordinary-wins-big-among-8-grand-prix-winners-from-cannes-lions-2026-day-1/"),
 # ③ The Ordinary Periodic Fable H&W GP (6/22)
 ("The Ordinary '주기율표 패러디', 칸 헬스&웰니스 GP 수상",
  "스킨케어 브랜드 The Ordinary의 'Periodic Fable'이 칸 라이언즈 Health & Wellness 그랑프리를 수상했다. 주기율표를 패러디해 '기공 제로'·'에이지 디파잉' 등 근거 없는 뷰티 마케팅 용어 49개를 폭로하며 소비자를 미혹하는 업계 관행에 정면 도전했다. Uncommon Creative Studio 제작으로, 브랜드가 자기 업계의 관행을 비판해 신뢰를 얻는 역공 마케팅의 교과서 사례다.",
  "MM+M", "https://www.mmm-online.com/news/the-periodic-fable-takes-home-cannes-lions-2026-health-and-wellness-grand-prix/"),
 # ④ 현대차 Coquí Alarmed 오디오 GP (6/22)
 ("현대차 'Coquí Alarmed', 칸 오디오 GP — 개구리 경보음 캠페인",
  "BBDO 푸에르토리코가 현대차 렌터카용으로 제작한 'Coquí Alarmed'가 칸 라이언즈 Audio & Radio 그랑프리를 받았다. 차량 잠금 경보음을 푸에르토리코 토종 개구리 '코키' 울음소리로 교체해 관광객의 불평을 문화 자부심 캠페인으로 역전시켰다. 불편함을 브랜드 자산으로 전환한 발상이 오디오 광고의 새 가능성을 보여준다.",
  "Campaign Live", "https://www.campaignlive.co.uk/article/hyundai-bbdo-puerto-rico-win-audio-radio-grand-prix-cannes-lions/1962418"),
 # ⑤ SKF 페로 제도 우주 프로그램 B2B GP (6/22)
 ("SKF '페로 제도 우주 프로그램', B2B 그랑프리 — 조류에너지를 우주 서사로",
  "스웨덴 베어링 제조사 SKF와 NORD Stockholm이 만든 'Faroe Islands Space Program'이 Creative B2B 그랑프리를 수상했다. 조류 에너지 수중 연 'Luna'를 중심으로 우주탐사 서사를 입혀 2030년 재생에너지 100% 목표를 가진 페로 제도 프로젝트를 홍보했다. B2B 광고가 B2C 못지않게 강렬한 스토리텔링으로 공감을 이끌 수 있음을 증명한 사례다.",
  "LBBOnline", "https://lbbonline.com/news/Cannes-Lions-2026-Grand-Prix-Winners-in-Audio-and-Radio-Creative-B2B-Creative-Brand-Health-and-Wellness-Outdoor-Pharma-Print-and-Publishing-and-the-Grand-Prix-for-Good"),
 # ⑥ Caritas 교황 차량→소아 이동클리닉 그랑프리 (6/22)
 ("교황 차량→가자 소아 이동클리닉 — 칸 Grand Prix for Good 수상",
  "Caritas Sweden과 스톡홀름 에이전시 Differ의 'Vehicle of Hope'가 Lions Health Grand Prix for Good를 수상했다. 교황 프란치스코가 2014년 요르단강 서안 방문 시 사용한 팝모빌을 가자지구 아동 이동 진료소로 개조해 하루 200명을 치료하는 것이 목표다. 이에 영감받아 7개 추가 클리닉이 대기 중이며, 브랜드 자산 활용과 인도적 임팩트를 연결한 모범 사례로 꼽혔다.",
  "MM+M", "https://www.mmm-online.com/news/caritas-vehicle-of-hope-takes-home-cannes-lions-health-grand-prix-for-good/"),
 # ⑦ FIFA 월드컵 2026 브랜드 마케팅 전쟁 (6/22~진행)
 ("FIFA 월드컵 2026, 105억 달러 브랜드 마케팅 전쟁 본격화",
  "FIFA 월드컵 2026 개막과 함께 유니레버·아디다스·나이키·코카콜라가 총 105억 달러 추가 광고비를 쏟아붓고 있다. 유니레버는 멕시코시티·뉴욕·마이애미에 'House of Fresh' 체험 허브를 운영하고, 코카콜라는 메시·베컴 등 WhatsApp 채널로 실시간 반응을 공유하며 퍼스트파티 데이터를 축적한다. 이번 월드컵은 스포츠 마케팅이 브랜드 활성화→퍼포먼스 전환의 통합 채널로 진화한 기점으로 기록될 전망이다.",
  "Adweek", "https://www.adweek.com/brand-marketing/fifa-world-cup-26-ad-tracker-brands-kick-off-summer-of-soccer/"),
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
    _seen_urls: set = set()
    def _force(url):
        if url in _seen_urls: return True
        _seen_urls.add(url); return False
    pages.append(cover(DATE, n_articles))
    idx = 2
    for cat_en, cat_ko, ac, suffix, items in SECTIONS:
        for item in items:
            t, b, s, u = item[:4]
            # 5번째 요소 = 이미지 전용 URL (og:image가 막힌 사이트 우회용)
            # 없으면 기사 URL 그대로 사용
            img_src = item[4] if len(item) > 4 else u
            forced = _force(u)
            link = safe_link(u)
            pages.append(card(idx, total, cat_en, cat_ko, ac, t, b, s, link,
                              f"{idx:02d}_{suffix}.png", force_search=forced,
                              img_url=img_src))
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
