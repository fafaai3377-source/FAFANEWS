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

# ── 2026-06-17 (수) 브리핑 — 6/16(화)~6/17(수) 24시간 ─────────────────────
# ⚠️ 전날(6/16) 기사와 중복 없음 — 모두 신규
AI = [
 ("SpaceX, AI 코딩 스타트업 Cursor 6조원 인수",
  "SpaceX가 AI 코딩 어시스턴트 Cursor를 만든 Anysphere를 600억 달러 규모 주식 딜로 인수한다고 6월 16일 발표했다. Cursor는 연간 경상수익 26억 달러를 돌파한 고성장 SaaS로, 스페이스X IPO 직후 AI 코딩 분야에 뛰어드는 머스크의 행보다. Claude Code·GitHub Copilot 등 경쟁 AI 코딩 도구들과의 본격 대결이 시작됐다.",
  "TechCrunch", "https://techcrunch.com/2026/06/16/spacex-to-acquire-cursor-for-60b-in-stock-days-after-blockbuster-ipo/"),
 ("VivaTech 2026 파리 오늘 개막 — 젠슨 황 AI 팩토리 키노트",
  "유럽 최대 AI·스타트업 행사 VivaTech 2026이 6월 17~20일 파리 엑스포에서 오늘 개막했다. NVIDIA CEO 젠슨 황이 'GTC 파리' 기조연설에서 AI 팩토리·소버린 AI·피지컬 AI를 주제로 발표한다. Yann LeCun·Arthur Mensch가 함께 무대에 서며 유럽 AI 전략과 주권 컴퓨팅 논의가 정점에 달한다.",
  "TechTimes", "https://www.techtimes.com/articles/318178/20260610/nvidia-gtc-paris-keynote-headlines-vivatech-june-17-20ai-factories-sovereign-ai-robotics-europe.htm"),
 ("Salesforce, Fin(인터콤) 3.6조원 인수 — AI 고객서비스 패권",
  "Salesforce가 AI 고객서비스 플랫폼 Fin(구 Intercom)을 36억 달러에 인수한다고 6월 15일 발표했다. Fin은 라이브채팅·왓츠앱·전화·슬랙 등 전채널을 자율 처리하는 AI 에이전트로 고객사 3만 개를 보유한다. Agentforce와 통합하면 Salesforce는 CRM부터 프런트라인 AI 에이전트까지 고객 접점 전 영역을 지배하게 된다.",
  "TechCrunch", "https://techcrunch.com/2026/06/15/salesforce-acquires-ai-customer-service-platform-fin-for-3-6b/"),
 ("Anthropic, 비밀 IPO 신청 — $965B 밸류 10월 상장 목표",
  "Anthropic이 6월 1일 SEC에 비밀 IPO 등록신청서(S-1)를 제출했다. 5월 완료한 650억 달러 시리즈H 투자에서 밸류에이션 9,650억 달러를 인정받아 OpenAI를 처음으로 추월했다. 골드만삭스·JPMorgan·모건스탠리가 주간사로, 역사상 최대 규모 AI 기업 상장 중 하나가 될 전망이다.",
  "Fortune", "https://fortune.com/2026/06/01/anthropic-confidentially-files-ipo-965-billion-valuation/"),
 ("Microsoft Agent 365 — 기업 내 'Shadow AI' 거버넌스 출시",
  "마이크로소프트가 AI 에이전트 감사·보안 솔루션 Agent 365를 정식 출시했다. 조직 내 무허가 AI 도구 사용(Shadow AI)을 탐지·통제하는 대시보드로 MS 조사 결과 직원의 29%가 이미 비허가 AI를 업무에 사용 중이다. KPMG가 첫 글로벌 파트너로 합류, 엔터프라이즈 AI 거버넌스가 새로운 CTO 필수 과제로 부상했다.",
  "VentureBeat", "https://venturebeat.com/technology/microsoft-takes-agent-365-out-of-preview-as-shadow-ai-becomes-an-enterprise-threat"),
 ("Probably, $900만 유치 — AI 환각 0% 99.99% 정확도 도전",
  "AI 신뢰성 스타트업 Probably가 6월 16일 900만 달러 시드 투자를 유치했다. AI 출력값의 오류를 수학적으로 검증해 99.99%의 정확도를 달성하는 것을 목표로 한다. 환각(hallucination) 문제로 엔터프라이즈 AI 도입을 망설이는 기업들을 타깃으로, '신뢰할 수 있는 AI' 인프라 레이어가 새로운 투자 테마로 부상 중이다.",
  "TechCrunch", "https://techcrunch.com/2026/06/16/probably-raises-9m-to-build-a-more-reliable-kind-of-ai/"),
 ("Writer AI 에이전트 — 프롬프트 없이 자율 실행, Amazon·MS 도전",
  "엔터프라이즈 AI 플랫폼 Writer가 사람의 지시 없이 스스로 작업을 계획하고 실행하는 자율 AI 에이전트를 출시했다. 마케팅 콘텐츠 생성, 데이터 분석, 워크플로 자동화를 프롬프트 없이 처리하며 Amazon·Microsoft·Salesforce의 에이전트 생태계에 도전장을 냈다. '에이전트-퍼스트' 엔터프라이즈 플랫폼 경쟁이 2026년 하반기 최대 전쟁터가 될 전망이다.",
  "VentureBeat", "https://venturebeat.com/technology/writer-launches-ai-agents-that-can-act-without-prompts-taking-on-amazon-microsoft-and-salesforce"),
]

DESIGN = [
 # ① 브랜딩 — Schweppes × JKR 헤리티지 리브랜딩
 ("Schweppes × JKR — 280년 세계 최초 탄산음료 리뉴얼",
  "세계 최초 탄산음료 슈웹스가 글로벌 브랜드 에이전시 JKR과 함께 대형 리브랜딩을 단행했다. 1851년 런던 만국박람회 분수 아이콘을 현대적으로 재해석하고 상징 캐릭터 '클라이브 레오파드'를 복원했다. 2026 Dieline 어워드 소프트드링크 부문 1위를 수상하며 헤리티지 브랜딩의 교과서로 자리매김했다.",
  "Creative Boom", "https://www.creativeboom.com/news/with-time-comes-taste-schweppes-reimagines-the-brand-it-invented/"),
 # ② 한국 브랜딩 — 대한항공 × Lippincott 40년 만의 리뉴얼
 ("대한항공 × Lippincott — 40년 만의 태극마크 새단장",
  "대한항공이 글로벌 브랜드 컨설팅사 Lippincott과 협업해 1984년 이후 첫 브랜드 리뉴얼을 완료했다. 한국 전통 무용 상모놀이의 리본 소용돌이에서 영감을 받아 태극 심볼을 재해석했으며 기체 도장·기내 인테리어까지 전면 변경된다. 아시아나 통합 이후 '세계 5대 프리미엄 항공사' 포지셔닝을 시각 언어로 구현한 전략적 리브랜딩이다.",
  "Creative Boom", "https://www.creativeboom.com/news/korean-air-unveils-elegant-new-brand-identity-in-collaboration-with-lippincott/"),
 # ③ 가구/공간 — 코펜하겐 3 Days of Design
 ("코펜하겐 '3 Days of Design' — 가구·조명 신제품 8선",
  "6월 10~12일 코펜하겐에서 열린 '3 Days of Design 2026'에 400개 이상 글로벌 브랜드가 가구·타일·조명 신제품을 선보였다. 올해 테마 'Make This Moment Matter'를 반영해 스칸디나비아 장인 정신과 자연 소재를 결합한 신제품들이 주목받았다. Dezeen이 엄선한 주목 신제품 8선에 바닥재·패브릭 조명·모듈러 가구가 고루 포함됐다.",
  "Dezeen", "https://www.dezeen.com/2026/06/12/products-tiles-furniture-3-days-of-design-2026/",
  "https://www.wallpaper.com/design-interiors/design-events/3-days-of-design-2026-copenhagen-preview"),
 # ④ 이벤트 — New Designers 2026 런던
 ("New Designers 2026 런던 7/1 개막 — 42년 만에 1주 통합",
  "영국 최대 졸업생 디자인 박람회 'New Designers'가 7월 1~4일 런던 이즐링턴 비즈니스 디자인센터에서 열린다. 42년 만에 기존 2주 포맷을 1주 통합 개최로 전환해 패션·가구·UX·게임 등 100개 이상 학과 2,500여 명의 졸업작품이 한자리에 모인다. 고용주와 크리에이터가 한 번에 만나는 구조 혁신이 업계의 주목을 받고 있다.",
  "It's Nice That", "https://www.itsnicethat.com/articles/new-designers-2026-creative-industry-sponsored-content-040626"),
 # ⑤ 타이포그래피 — 6월 새 서체
 ("6월 주목 새 서체 12선 — Unifora·스텐실·감성 디스플레이",
  "Creative Boom이 선정한 2026년 6월의 베스트 새 서체 12종이 공개됐다. Yep! 스튜디오의 Unifora는 산업적 엣지와 건축적 감성을 결합한 대형 sans-serif 수퍼패밀리로 가장 주목받는다. 자신의 규칙을 스스로 깨는 스텐실 서체부터 감성적 디스플레이 페이스까지 2026년 타이포그래피의 다양한 실험 방향이 담겼다.",
  "Creative Boom", "https://www.creativeboom.com/resources/the-best-new-typefaces-for-june-2026/"),
 # ⑥ 트렌드 — Stocksy 2026 비주얼 인사이트 리포트
 ("Stocksy 2026 비주얼 리포트 — AI 반작용·인간 중심 5 트렌드",
  "Stocksy의 '2026 비주얼 인사이트 리포트'가 대담함·인간 중심·감각적 풍요·의도적 불완전·커뮤니티 연결을 5대 핵심 트렌드로 제시했다. AI·AR 기술의 역풍으로 아날로그·유기적 표현이 강세를 보이는 것이 특징이다. 브랜드 비주얼 전략을 수립할 때 참고할 핵심 데이터 기반 인사이트다.",
  "Creative Boom", "https://www.creativeboom.com/insight/stocksys-2026-visual-insights-report-pinpoints-5-key-trends-that-are-reshaping-creative-culture-/"),
 # ⑦ 스튜디오 — Creative Boom 선정 주목할 스튜디오 15
 ("Creative Boom 선정 '지금 주목할 스튜디오 15'",
  "Creative Boom이 '뻔한 빅네임 너머'를 콘셉트로 2026년 현재 가장 주목받는 신진·차세대 디자인 스튜디오 15곳을 선정했다. 정체성이 선명하고 실험적이며 특정 영역에서 독보적인 스튜디오들이 포함됐다. 글로벌 크리에이티브 커뮤니티에서 빠르게 확산되며 스튜디오 벤치마킹 자료로 활용되고 있다.",
  "Creative Boom", "https://www.creativeboom.com/inspiration/15-studios-creatives-are-excited-about-right-now-beyond-the-obvious/"),
 # ⑧ 어워드 — 브랜드 임팩트 어워드 2026 접수
 ("브랜드 임팩트 어워드 2026 접수 시작 — 마감 7/9",
  "13회를 맞이하는 글로벌 브랜딩 어워드 '브랜드 임팩트 어워드 2026'의 접수가 시작됐다. 마감은 7월 9일이며, 아이덴티티·패키징·캠페인·디지털 경험 등 다양한 카테고리에서 수상작을 선정한다. 세계 각지의 브랜드 에이전시와 크리에이티브팀이 참가하는 주요 글로벌 브랜딩 경쟁 무대다.",
  "Creative Bloq", "https://www.creativebloq.com/design/branding/the-brand-impact-awards-2026-are-officially-open-for-entries"),
 # ⑨ 인물 — Top 20 그래픽 디자이너 2026
 ("Top 20 그래픽 디자이너 2026 — 크리에이터 투표 결과",
  "Creative Boom이 크리에이터 투표로 선정한 '2026년 가장 영향력 있는 그래픽 디자이너 20인'을 공개했다. 개성 강한 일러스트레이터·타이포그래퍼·브랜드 디자이너가 고루 포함됐으며 신진 이름도 다수 포진했다. '나의 인스피레이션은 누구인가'를 묻는 화제의 리스트로 글로벌 디자인 커뮤니티에서 활발히 공유되고 있다.",
  "Creative Boom", "https://www.creativeboom.com/inspiration/top-20-graphic-designers-of-2026-as-voted-for-by-creatives/"),
 # ⑩ 트렌드 — 크리에이터들이 질린 2026 디자인 트렌드
 ("크리에이터들이 이미 질린 2026 디자인 트렌드 10",
  "Creative Boom이 업계 크리에이터들의 의견을 모아 '2026년 이미 질린 디자인 트렌드 10가지'를 발표했다. AI 생성 이미지의 과도한 범람, 지나친 그라데이션 메시, 개성 없는 미니멀리즘 등이 포함됐다. '다음에 뭘 해야 하나'를 고민하는 크리에이터들에게 반면교사 인사이트를 제공하는 가이드다.",
  "Creative Boom", "https://www.creativeboom.com/insight/10-trends-creatives-are-so-over-in-2026/"),
 # ⑪ 캠페인 비주얼 — 아디다스 Backyard Legends
 ("아디다스 'Backyard Legends' — 메시·티모테·Bad Bunny",
  "아디다스가 FIFA 월드컵 2026을 위해 제작한 캠페인 'Backyard Legends'는 티모테 샬라메·리오넬 메시·Bad Bunny·라민 야말이 출연하는 브랜드 역사상 최대 규모 셀럽 앙상블 광고다. '전설은 뒷마당에서 태어난다'는 메시지로 90년대 감성을 구현했으며 이미 월드컵 제품 2,920억 원 이상을 판매했다. 스포츠 캠페인 비주얼 레퍼런스로 2026 최고 주목작이다.",
  "DesignRush", "https://news.designrush.com/best-fifa-world-cup-2026-ads"),
 # ⑫ 작가 — Ward Goes 실험적 그래픽 디자인
 ("Ward Goes — 타입과 물성의 경계를 탐구하는 디자이너",
  "네덜란드 디자이너 Ward Goes의 작업은 타이포그래피와 소재가 만나는 지점에서 펼쳐진다. 레이저 컷·인쇄·디지털 도구를 혼합해 종이 위에 문자와 소재가 공존하는 독자적인 시각 언어를 구축했다. It's Nice That이 '지금 발굴해야 할 그래픽 디자이너'로 소개하며 유럽 신진 디자인 씬에서 빠르게 주목받고 있다.",
  "It's Nice That", "https://www.itsnicethat.com/articles/ward-goes-graphic-design-discover-100226"),
]

MARKETING = [
 # ① M&A — Fox, Roku $22B 인수
 ("Fox, Roku $220억 인수 — CTV 광고 생태계 지각변동",
  "Fox Corporation이 스트리밍 플랫폼 Roku를 220억 달러에 인수한다고 6월 15일 발표했다. Fox 뉴스·스포츠·Tubi와 Roku의 1억 가구 도달 CTV 플랫폼이 결합하면 미국 TV 시청 점유율 3위 통합 플레이어가 탄생한다. 광고주 입장에서는 프리미엄 콘텐츠와 정밀 데이터 타기팅이 한 패키지로 묶이는 게임 체인저다.",
  "CNBC", "https://www.cnbc.com/2026/06/15/fox-to-buy-roku.html"),
 # ② 리포트 — Mediaocean H2 2026
 ("Mediaocean H2 2026 리포트 — AI 실전 도입 원년",
  "Mediaocean이 6월 16일 발표한 'H2 2026 마켓 리포트'에 따르면 마케터 75%가 AI를 1순위 트렌드로 꼽았으며, 10회 연속 최상위 트렌드를 유지하고 있다. 올해의 변화는 '실험'에서 '실전 구현'으로의 전환이며, AI가 플래닝·활성화·측정·최적화 전 과정에 통합되고 있다. 312개 브랜드·에이전시·미디어사 마케터 서베이를 기반으로 한다.",
  "BusinessWire", "https://www.businesswire.com/news/home/20260616484372/en/Mediaocean-Releases-2026-H2-Market-Report-Revealing-Marketers-Shift-from-AI-Hype-to-Hands-On-Implementation"),
 # ③ AI 광고 — ChatGPT 광고 CPC 전환
 ("ChatGPT 광고, CPM→CPC로 전환 — $25억 수익 목표",
  "OpenAI가 2월 출시한 ChatGPT 광고 플랫폼이 초기 CPM($60) 모델에서 CPC($3~5) 방식으로 전환했다. 10주 만에 연환산 1억 달러 수익을 넘겼으며 수백 곳의 광고주가 참여 중이다. 9억 명 주간 사용자를 보유한 ChatGPT 광고 시장이 2030년 1,000억 달러 규모로 성장할 것으로 전망된다.",
  "The Next Web", "https://thenextweb.com/news/openai-chatgpt-cpc-ads-launch"),
 # ④ 칸 — Titanium 쇼트리스트 공개
 ("칸 라이언즈 2026 Titanium 쇼트리스트 18편 공개",
  "칸 라이언즈가 2026 Titanium 쇼트리스트 18편을 발표했다. 형식·채널·장르의 경계를 뛰어넘는 혁신 작업에 수여되는 최고 영예 부문으로, BCP의 'SOS POS'와 하이네켄 'Tocayos'가 주목받는다. 전체 수상작 발표는 6월 22~26일 칸 축제 현장에서 이뤄진다.",
  "Roastbrief", "https://roastbrief.us/cannes-lions-2026-titanium-lions-shortlist-unveiled/"),
 # ⑤ CMO — Gartner 2026 AI 예산 서베이
 ("Gartner CMO 서베이 — 마케팅 예산의 15.3% AI에 투자",
  "Gartner의 '2026 CMO 지출 서베이'에 따르면 마케터들이 마케팅 예산의 15.3%를 AI 이니셔티브에 투자하고 있다. 그러나 AI를 스케일업할 준비가 됐다고 답한 비율은 30%에 그쳤다. AI 준비가 된 조직은 평균 21.3%를 AI에 배분하고 있어, '준비 격차'가 경쟁 우위의 핵심 요인이 되고 있다.",
  "Gartner", "https://www.gartner.com/en/newsroom/press-releases/2026-05-11-gartner-2026-cmo-spend-survey-finds-cmos-allocate-15-point-3-percent-of-marketing-budgets-to-ai-but-only-30-percent-are-ready-to-scale-ai-capabilities"),
 # ⑥ 스튜디오 통합 — MPC + The Mill
 ("MPC + The Mill 통합 — TransPerfect 단일 글로벌 스튜디오",
  "TransPerfect가 6월 16일 산하 VFX·크리에이티브 스튜디오 MPC와 The Mill을 'The Mill' 브랜드 아래 하나로 통합한다고 발표했다. 광고·패션·스포츠·게임을 담당하는 '브랜드&콘텐츠'와 영화·시리즈 VFX를 담당하는 '필름&시리즈' 두 사업부 체제로 재편한다. 2022년 Technicolor 붕괴 이후 재편이 이어지던 글로벌 크리에이티브 프로덕션 업계가 새 질서를 찾아가는 중이다.",
  "Adweek", "https://www.adweek.com/creativity/the-mill-and-mpc-merge-into-one-studio-under-transperfect/"),
 # ⑦ 캠페인 — Nike vs Adidas 월드컵 마케팅 대결
 ("Nike vs 아디다스 — 월드컵 $10조 광고 전쟁",
  "FIFA 월드컵 2026을 앞두고 Nike와 아디다스가 완전히 다른 전략으로 마케팅 전쟁을 벌이고 있다. 아디다스는 1억 달러 셀럽 군단(메시·티모테·Bad Bunny) 캠페인으로 감성에 승부를 걸었고, Nike는 알고리즘 기반 퍼스널라이즈드 마케팅을 택했다. 글로벌 브랜드들이 올해 월드컵에 쏟아붓는 광고비는 총 105억 달러를 넘어설 전망이다.",
  "Marketing Dive", "https://www.marketingdive.com/news/nike-adidas-take-rivalry-to-world-cup-who-will-win/822288/"),
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
