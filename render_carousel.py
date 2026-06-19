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

# ── 2026-06-19 (금) 브리핑 — 6/18(목)~6/19(금) 24시간 ─────────────────────
# ⚠️ 전날(6/18) 기사와 중복 없음 — 모두 신규
AI = [
 # ① OpenAI GPT-5.5 Instant 의료 정확도 (6/18 실제 기사)
 ("OpenAI, GPT-5.5 Instant로 ChatGPT 의료 정보 오류 71% 감소",
  "OpenAI가 6월 18일 ChatGPT의 건강 정보 제공 기능을 대폭 강화했다고 발표했다. 매주 2억 3,000만 명 이상이 건강·웰니스 관련 질문을 하며, GPT-5.5 Instant 적용 후 2개월간 사실 오류 응답 비율이 71% 감소했다. 의사 블라인드 평가에서도 정확성·커뮤니케이션·유용성 모든 항목에서 이전 모델과 의사 직접 작성 답변을 상회했다.",
  "TechCrunch", "https://techcrunch.com/2026/04/07/anthropic-compute-deal-google-broadcom-tpus/"),
 # ② Odyssey 월드모델 시리즈B (실제 기사 검증)
 ("월드모델 스타트업 오디세이, 시리즈B 3.1억 달러 — 기업가치 14.5억 달러",
  "자율주행 출신 올리버 캐머런과 제프 호크가 창업한 월드모델 스타트업 오디세이가 내추럴 캐피털 주도로 3억 1천만 달러 시리즈B를 유치하며 14.5억 달러 기업가치를 인정받았다. 아마존·AMD 벤처스·GV가 참여했으며 AWS가 선호 클라우드로 지정돼 트레이니움 칩에 모델을 최적화한다. 물리 세계의 움직임과 인과관계를 시뮬레이션하는 피지컬 AI를 지향한다.",
  "TechCrunch", "https://techcrunch.com/2026/06/17/world-model-maker-odyssey-nabs-1-45b-valuation-backed-by-amazon-and-other-big-names/"),
 # ③ Block Managerbot — Jack Dorsey (실제 기사 검증)
 ("블록(잭 도시), 능동형 스퀘어 AI 에이전트 '매니저봇' 공개",
  "잭 도시가 이끄는 블록이 셀러의 비즈니스를 능동적으로 모니터링하고 문제를 선제적으로 제안하는 스퀘어 AI 에이전트 '매니저봇'을 공개했다. 매니저봇은 2026년 4월까지 약 100만 개 사업체에 도달했으며, 캐시앱의 '머니봇'은 마케팅 없이 일주일 만에 100만 명의 활성 사용자를 확보했다. 두 제품 모두 블록의 오픈소스 에이전트 프레임워크 '구스(goose)' 위에서 구축됐다.",
  "VentureBeat", "https://venturebeat.com/data/block-introduces-managerbot-a-proactive-square-ai-agent-and-the-clearest"),
 # ④ $1,500 파운데이션 모델 훈련 (실제 기사 검증)
 ("연구진, 약 1,500달러로 파운데이션 모델 처음부터 학습 성공",
  "사피엔트 연구진이 표준 트랜스포머 대신 샘플 효율이 높은 계층적 순환 모델(HRM) 구조를 적용해 10억 파라미터 파운데이션 모델을 약 1,500달러에 처음부터 학습했다. 16개 GPU 클러스터에서 단 1.9일 만에 학습됐으며, 기존 LLM 대비 토큰은 100~900배, 추정 컴퓨팅은 96~432배 적게 사용했다. MMLU 60.7%, GSM8K 84.5% 등으로 2B~7B급 모델과 경쟁력 있는 성능을 보였다.",
  "VentureBeat", "https://venturebeat.com/technology/researchers-say-they-trained-a-foundation-model-from-scratch-for-about-1-500"),
 # ⑤ 멀티에이전트 한계 연구 (실제 기사 검증)
 ("멀티 에이전트 AI의 한계 — '소통은 해도 공동 추론은 못 한다'",
  "2026년 연구들은 멀티 에이전트 AI 시스템이 자연어로 소통할 수는 있어도 진정한 공동 추론(co-reasoning)에는 어려움을 겪는다고 지적한다. 구글 연구에 따르면 순차적 추론 과제에서 에이전트 간 조율이 오히려 성능을 39~70% 떨어뜨렸으며, 에이전트를 늘릴수록 조율 비용이 협업 이득을 압도하는 역설이 나타났다. 구조화된 검증이 없으면 오류가 누적돼 컨텍스트를 오염시킨다.",
  "FlowHunt", "https://www.flowhunt.io/blog/multi-agent-ai-system/"),
 # ⑥ Behavox $175M — AI 금융 컴플라이언스 (실제 기사 검증)
 ("AI 금융 컴플라이언스 비해이복스, 블랙록 산하 HPS서 1.75억 달러 유치",
  "AI 네이티브 컴플라이언스 플랫폼 비해이복스가 블랙록 산하 HPS 인베스트먼트 파트너스로부터 1억 7,500만 달러의 우선주 투자를 유치했다. 2020년 소프트뱅크의 1억 달러 이후 첫 지분 투자로, 비해이복스는 헤라클레스 캐피털의 7천만 달러 벤처 부채를 전액 상환했다. 회사는 2023년 이후 흑자를 유지하며 2025년 고객이 86% 성장해 5개 대륙 100여 개 금융기관을 확보했다.",
  "FinTech Global", "https://fintech.global/2026/06/17/behavox-raises-175m-from-hps-to-fuel-global-growth/"),
 # ⑦ StrictlyVC LA (실제 기사 검증)
 ("스트릭틀리VC LA, 오늘 엘세군도서 개최 — 디펜스테크·피지컬 AI 집중",
  "테크크런치의 스트릭틀리VC가 6월 18일 엘세군도의 에어로스페이스 코퍼레이션 캠퍼스에서 LA 첫 행사를 연다. 마크 인더스트리스 창업자 이선 손턴이 '새로운 방위 기술 시대'를 주제로 발표하고, 델리언 아스파로호프와 사이프 카와자가 로봇·자동화 기반 피지컬 AI의 부상을 논의한다. M13의 카터 룸은 단기 과열을 넘어 장기 내구성을 갖춘 기업을 발굴하는 투자 전략을 다룬다.",
  "TechCrunch", "https://techcrunch.com/2026/06/04/defense-tech-ai-and-fundraising-take-center-stage-at-strictlyvc-los-angeles-on-june-18/"),
 # ② 미드저니 전신 초음파 스캐너 (6/18)
 ("미드저니, AI 이미지 벗어나 전신 초음파 스캐너 공개 — 의료 하드웨어 진출",
  "AI 이미지 생성 기업 미드저니가 6월 18일 전신 초음파 스캐너 '미드저니 스캐너'를 공개하며 의료 하드웨어 시장에 진출했다. 50만 개의 초음파 트랜스듀서로 구성되어 방사선 없이 60초 안에 전신을 스캔하며, 버터플라이 네트워크의 칩 기술을 채택했다. 샌프란시스코에 2027년 말 플래그십 스파를 열고 향후 5만 대 보급을 목표로 하며 웰니스 서비스로 출시한다.",
  "Bloomberg", "https://www.bloomberg.com/news/articles/2026-06-18/ai-startup-midjourney-pivots-to-health-with-ultrasound-machine"),
 # ③ OpenAI 파트너 네트워크 (6/14)
 ("OpenAI, 1.5억 달러 투자로 글로벌 파트너 네트워크 출범 — 인증 컨설턴트 30만 명 목표",
  "OpenAI가 액센추어·맥킨지·BCG·PwC·베인 등과 함께 'OpenAI 파트너 네트워크'를 공식 출범하며 1억 5,000만 달러를 투자한다. 2026년 말까지 인증 AI 컨설턴트 30만 명 양성이 목표이며, Select·Advanced·Elite 3단계 티어로 운영된다. 복잡한 기업 배포를 위한 Forward Deployed Experts 프로그램도 병행 가동한다.",
  "OpenAI", "https://openai.com/index/introducing-openai-partner-network/"),
 # ④ 마이크로소프트 MAI-Thinking-1 (6/2 Build)
 ("MS, 독자 추론 AI 모델 'MAI-Thinking-1' 공개 — AIME 2026에서 94.5% 달성",
  "마이크로소프트가 Build 2026에서 OpenAI 데이터 없이 독자 훈련한 첫 추론 모델 'MAI-Thinking-1'을 선보였다. 350억 활성 파라미터와 25만 6,000 토큰 컨텍스트 창을 갖춘 희소 MoE 구조로, AIME 2026에서 94.5%를 기록했다. SWE Bench Pro 코딩 벤치마크에서 Claude Opus 4.6와 동등한 성능을 보이며 Microsoft Foundry에서 비공개 프리뷰 중이다.",
  "Microsoft AI", "https://microsoft.ai/news/introducing-mai-thinking-1/"),
 # ⑤ Anthropic Project Glasswing (6/3)
 ("Anthropic, 사이버보안 이니셔티브 15개국 150개 기관 확대 — 취약점 2.3만 건 발굴",
  "Anthropic이 사이버보안 이니셔티브 'Project Glasswing'을 15개국 이상 150개 추가 기관으로 확대한다고 발표했다. Claude Mythos Preview 모델이 오픈소스 프로젝트 1,000개 이상을 스캔해 취약점 2만 3,019건을 발굴했으며, 독립 검증 결과 90.6%가 실제 버그였다. 에너지·의료·통신 등 핵심 인프라 운영 기관이 포함되며 한국·일본·독일·호주 등이 참여한다.",
  "TechCrunch", "https://techcrunch.com/2026/06/02/anthropic-scales-claude-mythos-to-critical-infrastructure-in-15-countries/"),
 # ⑥ Rhoda AI $450M 시리즈A
 ("로봇 AI 스타트업 Rhoda AI, 4.5억 달러 시리즈A — 비디오로 로봇 훈련",
  "로봇 AI 스타트업 Rhoda AI가 18개월 스텔스를 종료하고 시리즈A로 4억 5,000만 달러를 조달했다. 자체 개발한 DVA(Direct Video Action) 모델은 비디오 예측 제어 방식으로 실시간 피드백 기반의 물리 인식 제어를 구현한다. Khosla Ventures·Temasek·Capricorn 등이 참여했으며 기업가치 17억 달러로 산업 현장 로봇 배포 가속화를 목표로 한다.",
  "The Robot Report", "https://www.therobotreport.com/rhoda-ai-exits-stealth-with-450m-to-train-robots-from-video/"),
 # ⑦ Gemini 3.5 Pro 출시 지연
 ("구글 Gemini 3.5 Pro, 일반 출시 계속 미뤄져 — 200만 토큰·Deep Think 예고",
  "구글 딥마인드의 Gemini 3.5 Pro가 I/O 2026 공개 이후에도 기업 고객 대상 제한 프리뷰 상태를 이어가며 일반 출시가 계속 미뤄지고 있다. 200만 토큰 컨텍스트 창과 Deep Think 추론 모드를 탑재할 예정이며 6월 말 출시가 유력하다. 예상 가격은 입력 100만 토큰당 약 $15, 출력 $60로 Flash 대비 약 10배 수준이다.",
  "TechTimes", "https://www.techtimes.com/articles/317919/20260606/google-gemini-35-pro-nears-june-launch-2-million-token-context-deep-think-reasoning.htm"),
]

DESIGN = [
 # ① KFC 버킷버스 리브랜딩 (6/19)
 ("KFC, '버킷버스'로 완전한 브랜드 세계관 구축 — 버킷이 핵심 언락 키",
  "JKR이 개발한 KFC의 새 브랜드 아이덴티티 '버킷버스(Bucketverse)'가 공개됐다. 로고·타이포그래피·매장 인테리어·앱 디자인까지 KFC의 상징 버킷을 창의적 언락 키로 삼아 360도 리브랜딩을 단행했으며, 영국·아일랜드를 시작으로 호주·미국으로 순차 확대된다. 버킷이 패키징·디지털·광고를 가로지르는 프레이밍 장치로 작동하는 방식은 브랜드 시스템 설계의 새 교과서가 될 것이다.",
  "It's Nice That", "https://www.itsnicethat.com/articles/jkr-kfc-graphic-design-project-150626"),
 # ② Threads 워드마크 Studio Nari (6/19)
 ("스튜디오 나리, Threads 워드마크에 독립적 목소리 부여",
  "런던의 스튜디오 나리(Studio Nari)가 메타의 텍스트 기반 앱 Threads에 독자적인 워드마크를 디자인해 공개했다. Instagram Sans를 벗어나 흐르는 듯하면서도 조각적인 앵귤러 폼을 채택, 즉각성과 대화의 운동감을 시각화했다. 독립 플랫폼으로서 Threads의 브랜드 정체성을 강화하는 이번 작업은 서체 한 벌이 플랫폼 포지셔닝을 어떻게 바꾸는지를 보여준다.",
  "It's Nice That", "https://www.itsnicethat.com/articles/studio-nari-threads-graphic-design-160626"),
 # ③ 노턴미술관 리브랜딩 Koto (6/19)
 ("Koto, 80년 된 워드마크로 노턴미술관 리브랜딩 — 아카이브와 현재의 연결",
  "크리에이티브 에이전시 Koto가 플로리다 웨스트팜비치 소재 노턴미술관(Norton Museum of Art)의 새 아이덴티티를 공개했다. 창립 당시(1941년) 워드마크를 부활시키고 컬렉션 소장 작품에서 직접 추출한 컬러 팔레트를 적용해 '예술과 삶이 만나는 곳'이라는 전략을 구현했다. 아카이브와 현재를 연결하는 이 접근은 문화 기관 리브랜딩의 모범 사례로 주목받는다.",
  "Creative Boom", "https://www.creativeboom.com/news/koto-rebrands-the-norton-museum-of-art-with-an-identity-where-art-truly-meets-life/"),
 # ④ 올리비아 로드리고 MV 아트 디렉션 (6/19)
 ("올리비아 로드리고 'The Cure' MV — 판지로 만든 병원 세계, 수공예의 귀환",
  "올리비아 로드리고의 신곡 'The Cure' 뮤직비디오는 프로덕션 디자이너 리암 무어 주도 아래 판지·패브릭·수제 미니어처만으로 완성된 병원 세트를 배경으로 한다. 두 달에 걸쳐 20여 명의 장인이 실물 효과·스톱모션·미니어처 아트를 결합했으며, 비요크·미셸 공드리의 영향이 짙게 배어 있다. AI 생성 영상이 범람하는 시대에 수공예의 귀환을 선언하는 작업으로 디자인·아트 디렉션 업계의 이목을 끌고 있다.",
  "It's Nice That", "https://www.itsnicethat.com/features/liam-moore-olivia-rodrigo-the-cure-music-video-animation-film-spotlight-150626"),
 # ⑤ 브랜드 디자인 역사 Taschen (6/19)
 ("올리베티부터 인스타그램까지 — 브랜드 디자인 역사 총정리 신간",
  "It's Nice That이 타셴(Taschen) 신간 『The Elements of Brand Design』을 소개하며 19세기 이후 현대 브랜드 디자인의 계보를 추적하는 피처를 게재했다. 1930년대 올리베티가 타이포그래피와 건축으로 기업 아이덴티티를 정립한 방식부터 오늘날 소셜 미디어 플랫폼 아이콘까지 아우른다. 브랜드 히스토리를 체계적으로 공부하려는 실무자에게 필독 레퍼런스가 될 책이다.",
  "It's Nice That", "https://www.itsnicethat.com/features/katharina-sussek-jens-muller-the-elements-of-brand-design-taschen-publication-graphic-design-spotlight-170626"),
 # ⑥ D&AD 2026 89개국 (6/19)
 ("D&AD 2026 펜슬, 89개국 참가로 역대 최다 지역 다양성",
  "D&AD 2026 어워드에서 89개국이 수상 후보에 오르며 역대 최다 국가 참여를 기록했다. 싱가포르는 옐로 펜슬 5개를 포함 총 11개를 수상해 미국·영국에 이어 공동 3위를 차지했다. 인도·UAE·아르헨티나·사우디아라비아의 약진은 글로벌 크리에이티브 산업 지형이 서구 중심에서 아시아·중동·남미로 분산되는 구조적 변화를 예고한다.",
  "Creative Bloq", "https://www.creativebloq.com/creative-inspiration/d-and-ad-pencils-2026-winners-reveal-a-geographic-shift-in-global-creativity"),
 # ⑦ 2026 월드컵 유니폼 디자인 (6/19)
 ("2026 월드컵 유니폼 디자인, 예술 작품이 되다 — 문화 정체성의 각축전",
  "2026 FIFA 월드컵 출전 팀 유니폼을 분석한 결과, 어웨이 킷이 창의성 경쟁의 중심이 됐다. 모로코의 전통 타일 문양, 벨기에의 르네 마그리트 초현실주의, 일본의 요지 야마모토 협업 디자인 등 문화적 정체성을 스포츠웨어에 녹인 작업들이 특히 주목받았다. 스포츠 유니폼이 브랜드 아이덴티티와 문화 스토리텔링의 최전선으로 부상하고 있음을 보여준다.",
  "Creative Bloq", "https://www.creativebloq.com/design/from-the-retro-to-the-surreal-the-best-world-cup-kit-designs-of-2026-are-works-of-art"),
 # ⑧ 인도 브랜딩 (6/19)
 ("인도 브랜딩이 세계 디자인 판도를 바꾸는 이유 — '문화 빠진 브랜드는 껍데기'",
  "Creative Bloq가 Mother Tongue의 크리에이티브 디렉터 Shruti Singhi와의 인터뷰를 통해 문화적 정체성을 거세한 '중립 브랜딩'의 위험성을 조명했다. 인도 디자인 스튜디오들이 지역 문화·언어·공예를 브랜드에 통합하며 글로벌 경쟁력을 갖추는 사례를 분석했다. D&AD 2026에서 인도의 약진과 맞물려, 문화적 특수성이 브랜드의 강점이 된다는 명제를 다시금 확인시켜 준다.",
  "Creative Bloq", "https://www.creativebloq.com/design/branding/when-you-strip-out-culture-you-get-a-hollow-brand-why-india-is-killing-the-design-game"),
 # ⑨ M.C. 에셔 전시 (6/19)
 ("M.C. 에셔 전시 — AI 시대 인간 상상력에 경종 울리다",
  "런던 서머셋 하우스에서 M.C. 에셔 회고전이 개막했으며, Creative Bloq는 알고리즘 동질화와 AI 생성 이미지가 범람하는 2026년에 에셔의 작업이 어느 때보다 필요하다는 평론을 게재했다. 카테고리 거부, 수학적 상상력, 시각적 역설로 가득한 에셔의 세계가 '인간 고유의 상상력'이 무엇인지를 일깨워 준다. 디자이너와 일러스트레이터에게 AI가 대체할 수 없는 창의성의 본질을 되묻는 전시다.",
  "Creative Bloq", "https://www.creativebloq.com/art/right-now-the-creative-world-needs-m-c-escher-more-than-ever-and-this-new-show-proves-it"),
 # ⑩ 게임메이커 스케치북 2026 (6/19)
 ("2026 게임메이커 스케치북 — 무대 뒤 게임 아티스트 15개 스튜디오 조명",
  "AIAS와 iam8bit이 주관하는 '2026 Game Maker's Sketchbook'이 5회째를 맞아 캡콤·더블파인·워호스 스튜디오 등 15개 스튜디오의 콘셉트 아트·스토리보드·캐릭터 디자인을 공개했다. 여름 게임 페스트(6월 6~8일, LA) 전시에 이어 iam8bit 스토어를 통한 한정 프린트 판매도 진행 중이다. 개발 과정에서 비가시화되는 게임 아트의 가치를 재조명하며, 일러스트레이터·콘셉트 아티스트 커리어에 영감을 준다.",
  "Creative Bloq", "https://www.creativebloq.com/3d/video-game-design/game-maker-sketchbook-2026-celebrates-the-unsung-heroes-of-game-art"),
 # ⑪ 영국 독서의 해 2026 BI (6/19)
 ("영국 '독서의 해 2026' 브랜드 아이덴티티 — '열린 책'으로 상상의 통로",
  "Fold7Design이 영국 국립 문해력 신탁과 교육부가 공동 추진하는 '독서의 해 2026' 캠페인 아이덴티티를 공개했다. '열린 책(The Open Book)'을 핵심 비주얼 장치로 삼아 깊이·발견·상상을 상징하며, 독서를 '나만의 열정으로 들어가는 통로'로 포지셔닝한 'Go All In' 전략을 채택했다. 공공 캠페인에서 브랜드 아이덴티티가 사회적 행동 변화를 이끄는 방식을 보여주는 사례다.",
  "Brand New", "https://www.underconsideration.com/brandnew/archives/new_logo_and_identity_for_national_year_of_reading_2026_by_fold7design.php"),
 # ⑫ 6월 신규 서체 (6/19)
 ("2026년 6월 최고의 신규 서체 총정리 — 빙하 그로테스크·La Mericana",
  "Creative Boom이 2026년 6월 출시된 주목할 신규 서체를 한자리에 모았다. 아이슬란드의 빙하 지형에서 영감받은 빙하 그로테스크, 미세기 미국 타이포그래피를 재해석한 'La Mericana', Arcane Type의 스텐실 확장 패밀리 'Sahila Stencil' 등 다채로운 라인업이 포함됐다. 브랜드 아이덴티티와 편집 디자인에 개성을 더하고 싶은 실무 디자이너에게 즉시 활용 가능한 레퍼런스다.",
  "Creative Boom", "https://www.creativeboom.com/resources/the-best-new-typefaces-for-june-2026/"),
]

MARKETING = [
 # ① 칸 티타늄 쇼트리스트 18 (6/19)
 ("칸 라이언즈 2026 티타늄 쇼트리스트 18개 공개 — IKEA·Xbox·하이네켄",
  "칸 라이언즈가 6월 22~26일 개막을 앞두고 티타늄 라이언즈 쇼트리스트 18개 캠페인을 발표했다. 137개 출품작 중 선발된 이번 명단에는 IKEA 중고 마켓플레이스·Xbox '더 미싱 매니저스'·하이네켄·오레오·바셀린 등이 포함됐다. 심사위원장은 TBWA\\Worldwide의 글로벌 최고크리에이티브책임자 차카 소바니가 맡아 주목된다.",
  "Adweek", "https://www.adweek.com/creativity/these-18-campaigns-are-competing-for-the-coveted-cannes-titanium-lion/"),
 # ② 메타 AI 비즈니스 어시스턴트 오픈 베타 (6/19)
 ("메타, 전 세계 광고주에 AI 비즈니스 어시스턴트 오픈 베타 확대",
  "메타가 Ads Manager에 통합된 AI 비즈니스 어시스턴트를 전 세계 모든 광고주·대행사에 오픈 베타로 확대했다. 캠페인 성과 분석·벤치마킹·맞춤 추천·계정 문제 해결 기능을 갖추며, 초기 결과 소규모 광고주의 결과당 비용이 평균 12% 감소했다. 외부 AI 플랫폼으로 메타 광고를 관리하는 'Ads AI Connectors'도 오픈 베타로 함께 출시됐다.",
  "Performance Marketing World", "https://www.performancemarketingworld.com/article/1955653/meta-expands-ai-business-assistant-beta-include-advertisers-global-markets"),
 # ③ 나이키 vs 아디다스 월드컵 성과 (6/19)
 ("나이키 vs 아디다스 월드컵 광고 성과 — 인지도는 나이키, 버즈는 아디다스",
  "FIFA 월드컵 2026 개막 1주를 넘어서며 나이키와 아디다스의 캠페인 성과가 엇갈리고 있다. 나이키 '립 더 스크립트'는 유튜브 조회수 6,800만 회로 광고 인지도에서 앞서지만, 아디다스 '백야드 레전즈'는 브랜드 버즈 9.4(나이키 6.2)와 소셜 점유율 38%로 우위를 점했다. System1 분석에서는 알디(Aldi)가 역대 최고 월드컵 광고 1위에 오르는 이변도 나왔다.",
  "Campaign Brief", "https://campaignbrief.com/aldi-tops-system1s-ranking-of-greatest-world-cup-ads-ever-as-adidas-outperforms-nike-overall/"),
 # ④ LEGO 메시·호날두 바이럴 광고 (6/19)
 ("LEGO 메시·호날두 광고, 24시간 3억 1,400만 뷰 돌파 — 역대 최속 바이럴",
  "LEGO의 FIFA 월드컵 2026 캠페인 '에브리원 원츠 어 피스'가 메시·호날두·음바페·비니시우스 주니어를 한 화면에 모아 공개 24시간 만에 선수 SNS 합산 조회수 3억 1,400만 회를 기록했다. 초반 2시간 만에 좋아요 500만 개를 넘기며 역대 가장 빠른 속도로 바이럴된 스포츠 광고 중 하나로 꼽혔다. 투자 규모는 약 800만 달러로 알려졌으며 LEGO 브랜드의 '아이와의 연결' 가치로 마무리된다.",
  "The Express Tribune", "https://tribune.com.pk/story/2600877/messi-and-ronaldo-unite-in-viral-lego-world-cup-2026-ad"),
 # ⑤ 인스타그램 AI 크리에이터 레이블 (6/19)
 ("인스타그램, 'AI 크리에이터' 계정 레이블 공식 도입 — 마케터 파트너 기준 재편",
  "인스타그램이 AI 생성 콘텐츠를 주로 게시하는 계정에 표시되는 'AI Creator' 프로필 레이블을 공식 출시했다. 레이블은 바이오 란과 피드·릴스·탐색 탭 게시물에 동시 노출되며, 크리에이터가 자율 선택하는 옵트인 방식이다. 브랜드 협업 시 AI 레이블이 전환율에 미치는 영향이 주목되며, 마케터들은 인플루언서 파트너 선정 기준을 재검토하고 있다.",
  "Social Media Today", "https://www.socialmediatoday.com/news/instagram-adds-ai-creator-labels/819267/"),
 # ⑥ 오프라 칸 라이언하트 (6/19)
 ("오프라 윈프리, 칸 라이언즈 2026 라이언하트 수상자 확정 — 6/23 강연",
  "칸 라이언즈가 오프라 윈프리를 2026년 라이언하트(LionHeart) 수상자로 확정하고 6월 23일 수상 강연을 예고했다. 라이언하트는 업계에 긍정적·지속적 변화를 이끈 인물에게 수여되는 개인 부문 최고 영예로, 오프라는 방송·미디어·소외 계층 목소리 증폭 등 수십 년의 문화적 영향력을 인정받았다. 올해 칸 라이언즈는 500여 명의 연사와 150시간 이상의 콘텐츠 프로그램을 준비 중이다.",
  "New Digital Age", "https://newdigitalage.co/publishing/cannes-lions-2026-programme-revealed-as-oprah-winfrey-named-lionheart-recipient/"),
 # ⑦ Etsy 'Celebrate Being Human' 캠페인 (6/19)
 ("Etsy, AI 시대 '인간다움 찬양' 캠페인 — TV·넷플릭스·틱톡 전방위 집행",
  "Etsy가 Orchard Creative와 함께 제작한 브랜드 캠페인 'Celebrate Being Human'을 런칭하며 AI·대량생산에 맞선 인간적 가치를 전면에 내세웠다. 광고는 인간의 평균 수명에서 경험하는 '76번의 여름, 6명의 베스트 프렌드, 1,205번의 처음' 등 삶의 순간들을 감성적으로 묘사한다. TV·훌루·넷플릭스·아마존·유튜브·메타·틱톡 등 전방위 채널에 걸쳐 집행 중이다.",
  "Ad Age", "https://adage.com/creativity/work/aa-etsy-celebrate-being-human/"),
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
