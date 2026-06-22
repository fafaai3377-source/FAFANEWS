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

# ── 2026-06-22 (월) 브리핑 — 6/19(금)~6/22(월) 72시간 ─────────────────────
# ⚠️ 전날(6/19) 기사와 중복 없음 — 모두 신규
AI = [
 # ① EU 오픈소스 AI 모델 EUROPA 컨소시엄 (6/19)
 ("EU, 자체 오픈소스 AI 모델 개발사 선정",
  "유럽연합 집행위원회가 6월 19일 이탈리아 기업 Domyn이 이끄는 EUROPA 컨소시엄을 'Frontier AI Grand Challenge' 수상자로 선정했다. 해당 컨소시엄은 EU 24개 공식 언어를 지원하는 4,000억 파라미터 이상의 오픈소스 AI 모델을 구축하며, NVIDIA Blackwell 칩 6,000개 전용 클러스터를 지원받는다. 빅테크 의존에서 벗어난 유럽의 AI 주권 확보 전략의 핵심 이정표로 기업·연구자·공공기관 모두에 무료 공개될 예정이다.",
  "European Commission", "https://digital-strategy.ec.europa.eu/en/news/commission-selects-europa-consortium-winner-frontier-ai-grande-challenge-project-build-european"),
 # ② Anthropic 서울 오피스 + 한국 파트너십 (6/17~18)
 ("Anthropic, 서울 오피스 개설·LG·삼성·네이버 파트너십 발표",
  "Anthropic이 서울 오피스를 공식 개설하며 LG CNS·삼성SDS·NAVER·넥슨·한화솔루션 등 국내 주요 기업에 Claude를 배포하기로 했다. 과학기술정보통신부와 AI 안전성 강화 MOU도 체결했으며, KAIST·고려대·연세대·POSTECH 등으로 구성된 NAIRL 연구자 60명에게 Claude 접근권을 제공한다. IPO를 앞두고 아태 세 번째 거점을 확보하며 국내 엔터프라이즈 AI 시장 공략을 본격화했다.",
  "Anthropic Blog", "https://www.anthropic.com/news/seoul-office-partnerships-korean-ai-ecosystem"),
 # ③ OpenAI Codex Record & Replay (6/18)
 ("OpenAI Codex, 한 번 보여주면 자동 반복 기능 추가",
  "OpenAI가 Codex macOS 앱에 'Record & Replay' 기능을 출시했다. 사용자가 워크플로를 한 번 직접 수행하면 Codex가 이를 관찰해 재사용 가능한 자동화 스킬로 변환하며, 이후 Computer Use·브라우저 액션·플러그인과 결합해 무한 반복 실행이 가능하다. 별도 스크립트 작성 없이 AI 에이전트 자동화를 구현할 수 있어 ChatGPT Plus·Pro·Business 구독자의 반복 업무 자동화 진입 장벽이 크게 낮아졌다.",
  "The Decoder", "https://the-decoder.com/openais-codex-can-now-watch-you-work-once-and-repeat-the-task-forever/"),
 # ④ Fable 5 수출 금지 6/20 업데이트
 ("Fable 5 수출 금지, 6월 20일에도 지속 — 트럼프 '협상 중'",
  "6월 20일 기준 미국 정부의 수출 통제로 Fable 5와 Mythos 5의 전 세계 접근이 차단 중이다. 트럼프 대통령은 G7 정상회의에서 협상이 '잘 진행 중'이라고 언급했으나 공식 복구 일정은 발표되지 않았으며, 유료 구독자 대상 환불 신청 기간도 마감됐다. 이 사태는 미국 정부가 AI 모델 수출을 국가안보 자산으로 규제하는 새 선례를 만들고 있어 업계 전반에 중요한 리스크 신호다.",
  "TechTimes", "https://www.techtimes.com/articles/318760/20260620/fable-5-ban-update-trump-softens-directive-stands-refund-deadline-closes-today.htm"),
 # ⑤ Baseten AI 추론 인프라 15억 달러 (6/18)
 ("AI 추론 인프라 Baseten, 15억 달러 조달·기업가치 130억 달러",
  "AI 추론 인프라 스타트업 Baseten이 약 15억 달러 규모 펀딩을 마무리하며 기업가치 130억 달러로 5개월 만에 160% 상승했다. 연간 매출 실행률이 단 한 분기 만에 2억 달러에서 6억 달러로 3배 급증했고, 오픈소스 모델을 상시 구동하는 앱 수요 폭증이 원인이다. 파운데이션 모델이 범용화되면서 추론 인프라 레이어가 AI 스택의 핵심 수익 지점으로 부상하고 있다는 강력한 신호다.",
  "TechCrunch", "https://techcrunch.com/2026/06/18/ai-inference-startup-baseten-reportedly-raising-1-5b-months-after-its-last-mega-round/"),
 # ⑥ General Intuition 게임 영상 학습 AI 3억 달러 (6/18)
 ("게임 영상 학습 AI 'General Intuition', 3억 달러 유치",
  "뉴욕 스타트업 General Intuition이 게임 클립 3억 개/년 기반 월드모델 학습으로 3억 달러 투자 협상 중이며 기업가치는 20억 달러를 넘어섰다. 제프 베조스·에릭 슈미트가 신규 투자자로 참여하고, 8개월 전 시드 1억 3,400만 달러에 이은 두 번째 대형 라운드다. '게임 영상'이 실제 공간·시간 추론 능력을 갖춘 에이전트 학습의 최적 데이터로 주목받는 월드모델 투자 열풍을 보여준다.",
  "TechCrunch", "https://techcrunch.com/2026/06/18/general-intuition-in-talks-to-raise-300m-at-around-2b-valuation/"),
 # ⑦ 퓨리서치 미국인 AI 인식 조사 (6/17)
 ("퓨리서치: 미국인 16%만 AI가 사회에 이롭다 믿어",
  "퓨리서치센터 발표에 따르면 미국 성인의 49%가 AI 챗봇을 사용하지만, AI가 20년 내 사회에 긍정적 영향을 줄 것으로 믿는 비율은 16%에 불과하다. 63%는 AI 기술이 너무 빠르게 발전한다고 느끼며, 71%는 개인정보 보안을 우려한다. AI 채택률 급등과 사회적 신뢰 부재의 극심한 괴리는 기업의 AI 도입 커뮤니케이션 전략 수립에서 핵심 변수가 된다.",
  "TechCrunch", "https://techcrunch.com/2026/06/17/only-16-percent-of-americans-think-ai-will-have-a-positive-impact-on-society-a-new-study-shows/"),
]

DESIGN = [
 # ① 헤르만 밀러 에어런 체어 30년 만에 컬러 추가
 ("에어런 체어, 30년 만에 컬러 입다 — 올리브·미드나잇 블루",
  "헤르만 밀러가 아이코닉 에어런 체어에 올리브 그린 '재스퍼'와 미드나잇 블루 '나이트폴' 두 가지 컬러를 신규 추가했다. 지속가능성 개선으로 탄소 발자국을 12% 절감하고 플라스틱 재활용량을 두 배 이상 늘렸다. 폴튼 마켓 디자인데이즈를 통해 공개돼 6월 내내 화제를 모으고 있다.",
  "Wallpaper*", "https://www.wallpaper.com/design-interiors/furniture/herman-miller-aeron-office-chair",
  "https://news.millerknoll.com/2026-06-02-Herman-Miller-introduces-Aeron-Chair-in-color-and-advances-its-sustainable-and-inclusive-design"),
 # ② 이케아 PS 2026 공기주입식 의자 한국 출시
 ("이케아 PS 2026 공기주입식 의자, 한국 출시",
  "이케아가 1990년대 연구를 계승한 공기주입식 이지체어를 포함한 'IKEA PS 2026' 컬렉션 44종을 국내 6개 매장에서 판매 중이다. 크롬 튜브 프레임 안에 독립 에어 챔버 두 개를 장착한 구조로, 20개 프로토타입 끝에 완성된 기술적 성취다. 다기능·재미 중심의 스칸디나비아 민주적 디자인 철학을 실험적으로 구현한 컬렉션이다.",
  "핀포인트뉴스", "https://www.pinpointnews.co.kr/news/articleView.html?idxno=452735",
  "https://www.ikea.com/global/en/stories/design/ikea-ps-2026-collection/"),
 # ③ 밀러놀 무토 20주년 콜트레 소파
 ("밀러놀, 무토 20주년 '콜트레 소파' 조각적 형태로 공개",
  "밀러놀이 폴튼 마켓 디자인데이즈에서 무토(Muuto) 창립 20주년 기념 조각적 형태의 콜트레 소파를 프리뷰 공개했다. 70,000 sq ft 8개 층에 걸친 역대 최대 규모 쇼케이스로 지속가능·포용적 설계가 핵심 키워드였다. HAY의 팔리사드 캔틸레버 컬렉션 등도 함께 소개됐다.",
  "GeneOnline", "https://www.geneonline.com/millerknoll-displays-new-furniture-collections-at-2026-fulton-market-design-days/",
  "https://news.millerknoll.com/2026-06-03-MillerKnoll-Powers-the-Future-of-Design-at-Fulton-Market-Design-Days-2026"),
 # ④ 매리어트 본보이 호텔 감성 홈 리빙샵 론칭
 ("매리어트 본보이, '호텔 감성' 홈 리빙 숍 론칭",
  "매리어트 본보이 부티크가 W호텔·웨스틴 컬렉션을 시작으로 호텔 인테리어 감성의 가구·오브제를 판매하는 '디자인 숍'을 론칭했다. 6월에는 프랑스 리비에라 영감의 서빙 트레이·리넨·와인 고블릿 드롭이 추가된다. 호스피탈리티 브랜드가 리테일 라이프스타일 영역으로 확장하는 새로운 비즈니스 모델로 주목받는다.",
  "Wallpaper*", "https://www.wallpaper.com/travel/marriott-bonvoy-design-shop-launch"),
 # ⑤ 성수 '파묘' 팝업 (한국)
 ("성수 '파묘' 팝업 — 산업 창고를 퇴마 공간으로",
  "영화 '파묘' IP를 활용한 공식 팝업이 성수이로18길 세원정밀 산업 공간에서 6월 14일~23일 운영 중이다. 을씨년스러운 공장 건물 분위기와 영화의 무속·퇴마 세계관을 결합한 이머시브 공간 연출로 SNS에서 화제를 모으고 있다. 산업 유산 공간을 브랜드 경험으로 전환하는 성수만의 팝업 공식을 잘 보여주는 사례다.",
  "팝가", "https://popga.co.kr/content/magazine/284",
  "https://www.seongsudonggorilla.com/article/633"),
 # ⑥ 젠틀몬스터 X Bratz 글로벌 팝업 (한국)
 ("젠틀몬스터 X Bratz, 서울·LA·상하이 월드투어 팝업",
  "한국 아이웨어 브랜드 젠틀몬스터가 브라츠 인형 IP와 협업한 팝업을 서울·LA·상하이·방콕 4개 도시에서 동시 순회 운영한다. 각 도시마다 라벤더 톤 메탈릭 텍스처와 초대형 브라츠 조각상으로 SF적 드림월드를 구현하고, 전용 AI 부스에서 방문객을 브라츠 인형으로 변환하는 체험을 제공한다. 패션·팝컬처·AI 기술을 융합한 글로벌 공간 마케팅의 최신 사례다.",
  "The Impression", "https://theimpression.com/gentle-monster-opens-bratz-inspired-melrose-pop-up/"),
 # ⑦ 투썸플레이스 한글 자모 심벌 논란 (한국)
 ("투썸플레이스 한글 자모 심벌 논란 — '이게 진짜 로고냐'",
  "투썸플레이스의 한글 자모 'ㅆㅁ'과 영문 'T'를 조합한 새 심벌이 SNS에 퍼지며 디자인정글·온라인 커뮤니티에서 가독성 논쟁이 폭발했다. 회사 측은 '브랜드 2.0 시안 중 하나일 뿐 현행 로고 교체 계획 없다'며 진화에 나섰다. 한국 브랜드 로고가 가독성과 실험성 사이에서 어떤 균형을 잡아야 하는지 다시 생각하게 만드는 이슈다.",
  "디자인정글", "https://www.jungle.co.kr/magazine/207054"),
 # ⑧ 임페리얼 칼리지 런던 학과별 'I' 아이덴티티
 ("임페리얼 칼리지 런던, 24개 학과마다 고유한 'I' 로고",
  "디자인 스튜디오 더 클릭(The Click)이 임페리얼 칼리지 런던의 24개 학과를 위해 각 학과 성격에 맞는 고유한 'I' 레터폼 서브 브랜딩 시스템을 만들었다. 항공학과의 I는 활공하고 화학과의 I는 폴리머로 구성되는 식으로 학문적 특성이 시각 언어에 녹아든다. 대학 브랜딩의 경직된 통일성을 깨고 유연한 정체성을 설계한 사례다.",
  "It's Nice That", "https://www.itsnicethat.com/articles/the-click-imperial-college-london-graphic-design-project-200526"),
 # ⑨ 인터브랜드 자사 리브랜딩 (인하우스)
 ("인터브랜드, 외부 에이전시 없이 자사 로고 직접 리디자인",
  "글로벌 브랜드 컨설팅사 인터브랜드가 인하우스 팀 주도로 자사 브랜드 아이덴티티를 전면 개편했다. 외부 에이전시에 의존하지 않고 자사 역량으로 리브랜딩을 완수한 것 자체가 '우리가 하는 말을 우리가 실천한다'는 메시지다. Brand New가 방법론과 결과물을 심층 분석했다.",
  "Brand New", "https://www.underconsideration.com/brandnew/archives/new_logo_and_identity_for_interbrand_done_in_house_2026.php"),
 # ⑩ 칸 라이언즈 2026 디자인 쇼트리스트 (6/21)
 ("칸 라이언즈 디자인 쇼트리스트, AI 크래프트 부문 신설",
  "칸 라이언즈가 6월 21일 디자인 부문 최종 후보를 확정하며 AI 크래프트 서브카테고리를 2026년 처음 신설했다. 브랜드 아이덴티티·패키징·환경 디자인 등 3대 카테고리에 약 140편이 진출했으며 6월 22~26일 수상작이 발표된다. '비주얼 크래프트맨십'과 브랜드 연결성이 핵심 심사 기준이다.",
  "Cannes Lions", "https://www.canneslions.com/awards/lions/design"),
 # ⑪ Creative Boom 6월 '이상하게 브랜딩하라'
 ("Creative Boom: '브랜딩이 너무 안전해졌다 — 이상함이 살길'",
  "Creative Boom이 6월 'Booms & Shakes' 이슈에서 '브랜딩이 너무 무난해졌다'며 2026년에는 이상함(weird)이 브랜드를 살릴 수 있다고 주장했다. Mother Design의 디자인 디렉터가 '지난 12개월간 아이덴티티 디자인이 지나치게 sanitised됐다'며 리브랜딩 용기를 촉구했다. VIEVE 뷰티 등 실험적 브랜딩 프로젝트들이 이 흐름을 뒷받침한다.",
  "Creative Boom", "https://www.creativeboom.com/news/booms-and-shakes-june-2026/"),
 # ⑫ 칸 라이언즈 첫 수상작 및 그랑프리 (6/22)
 ("칸 라이언즈 2026 첫 수상작 발표 — 그랑프리 주목",
  "칸 라이언즈 2026이 6월 22일 개막 첫날 초기 부문 수상작을 발표했다. 올해 페스티벌은 AI·크리에이터 이코노미·비즈니스 임팩트를 3대 핵심 의제로 삼으며 신설 'Creative Brand Lion' 등 새 카테고리가 주목받고 있다. 수상작들은 단발성 캠페인 중심에서 지속 가능한 브랜드 시스템 구축으로 크리에이티브의 무게중심이 이동하고 있음을 보여준다.",
  "Cannes Lions", "https://www.canneslions.com/news/first-winners-announced-at-the-72nd-cannes-lions-international-festival"),
]

MARKETING = [
 # ① EA Advertising 인게임 광고 플랫폼 (6/15~19)
 ("EA, 인게임 브랜드 광고 플랫폼 'EA Advertising' 론칭",
  "일렉트로닉 아츠가 월 1억 2000만 플레이어에 도달하는 'EA Advertising' 플랫폼을 6월 15일 공식 출시했다. Coach·Visa·Lowe's·State Farm 등이 첫 파트너로 인게임 챌린지·바이럴 아이템·스폰서 콘텐츠를 EA Sports 타이틀 전반에 집행하며, Lowe's는 98만 7000건 이상의 인게임 플레이를 이끌었다. 브랜드의 TV 스포츠 예산이 게이밍 미디어로 이동하는 가속화 사례다.",
  "Marketing Dive", "https://www.marketingdive.com/news/ea-rolls-out-advertising-platform-with-enhanced-offerings-for-brands/822833/"),
 # ② 칸 라이언즈 2026 개막 (6/22)
 ("칸 라이언즈 2026 개막 — AI·크리에이터 이코노미 3대 의제",
  "6월 22일 개막한 제73회 칸 라이언즈 인터내셔널 페스티벌이 AI·크리에이터 이코노미·비즈니스 임팩트를 3대 핵심 의제로 제시했다. OpenAI·Google DeepMind·Meta 등 빅테크 CEO급 연사 500명 이상이 150시간 이상의 프로그램에 참여하며, 신설된 'Creative Brand Lion'은 캠페인이 아닌 브랜드 생태계 전반의 창의 역량을 평가한다. 역사상 최초 3회 수상 'Creative Marketer of the Year' AB InBev가 개막 기조연설을 맡았다.",
  "Cannes Lions", "https://www.canneslions.com/festival/programme"),
 # ③ Uber Eats 고든 램지 월드컵 캠페인
 ("Uber Eats × 고든 램지 '요리 하지 마!' 글로벌 캠페인",
  "Uber Eats가 Mother 제작 'Who Could Cook At A Time Like This?'를 6월 9일 론칭했다. 고든 램지가 주방에 난입해 월드컵 시청을 이유로 요리를 막는 17개국 TV·OOH·SNS 캠페인으로, 유명 셰프가 '배달 주문'을 독려하는 역설적 설정이 강렬한 브랜드 각인 효과를 냈다. Uber Eats 역사상 첫 글로벌 단일 캠페인으로 스포츠 이벤트 연계 브랜드 포지셔닝의 교과서 사례다.",
  "The Drum", "https://www.thedrum.com/news/ad-of-the-day-uber-eats-taps-gordon-ramsay-to-discourage-world-cup-cooking"),
 # ④ Lay's WhatsApp 월드컵 스타 채팅 마케팅
 ("Lay's, WhatsApp으로 팬 1000만 명 월드컵 마케팅",
  "PepsiCo의 Lay's가 메시·베컴·앙리·스티브 카렐이 참여한 WhatsApp 브로드캐스트 채널로 90개 이상 시장에서 팬 1000만 명 이상을 확보했다. 선수들이 매치 데이마다 보이스 노트·밈·반응을 공유해 퍼스트파티 데이터와 세컨드스크린 인게이지먼트를 동시에 달성했다. WhatsApp 채널을 CRM과 콘텐츠 미디어로 활용한 대규모 스포츠 마케팅 선례다.",
  "Adweek", "https://www.adweek.com/brand-marketing/fifa-world-cup-26-ad-tracker-brands-kick-off-summer-of-soccer/"),
 # ⑤ AB InBev 칸 라이언즈 역사상 최초 3회 수상
 ("AB InBev, 칸 라이언즈 역사상 최초 3회 연속 수상",
  "Anheuser-Busch InBev가 2022·2023년에 이어 2026 칸 라이언즈 'Creative Marketer of the Year'를 세 번째로 수상, 역대 최초 3회 수상 기업이 됐다. AB InBev는 지난해 페스티벌에서만 37개 라이언을 수상했으며, 신설 'Creative Brand Lion' 심사위원장도 겸임한다. 브랜드 포트폴리오 전반에 걸친 창의적 일관성이 성장 동력이 된다는 점을 입증한 사례다.",
  "Adweek", "https://www.adweek.com/creativity/ab-inbev-wins-cannes-lions-creative-marketer-of-the-year-for-a-historic-third-time/"),
 # ⑥ Instagram 릴스 트렌딩 광고 전 광고주 개방
 ("Instagram 릴스 트렌딩 광고, 전 광고주 개방 — 인지도 20% 향상",
  "Meta가 Instagram Reels Trending Ads를 전체 광고주에게 개방하면서 문화 맥락 콘텐츠 옆 집행 시 브랜드 인지도 최대 20% 향상 효과를 공개했다. 내부 59개 연구 기준 대조군 대비 광고 회상 6.6%p 추가 상승을 기록했으며, TV·영화·패션위크·NFL 등 '컬처럴 모멘트' 카테고리도 신규 추가됐다. 문화적 관련성을 고려한 맥락 광고 배치가 퍼포먼스 향상의 핵심이 됨을 보여준다.",
  "Variety", "https://variety.com/2026/digital/news/instagram-reels-trending-ads-tv-movies-cultural-moments-nfl-1236698010/"),
 # ⑦ 칸 라이언즈 Creative Brand Lion 신설
 ("칸 라이언즈 'Creative Brand Lion' 신설 — 캠페인 아닌 역량 평가",
  "칸 라이언즈 2026이 신규 부문 'Creative Brand Lion'을 신설해 캠페인이 아닌 브랜드 내부 시스템·문화·역량을 평가 기준으로 삼기 시작했다. 137개 엔트리 중 10개만 쇼트리스트에 진입했으며, 측정 가능한 비즈니스 성장·ROI·장기 브랜드 가치가 심사 기준이다. '단발 캠페인 수상'에서 '반복 가능한 창의 역량 보유 기업' 인정으로 업계 평가 패러다임이 전환되고 있음을 보여주는 구조적 변화다.",
  "Cannes Lions", "https://www.canneslions.com/news/cannes-lions-introduces-the-creative-brand-lion"),
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
