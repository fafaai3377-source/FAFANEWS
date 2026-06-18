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

# ── 2026-06-18 (목) 브리핑 — 6/17(수)~6/18(목) 24시간 ─────────────────────
# ⚠️ 전날(6/16·6/17) 기사와 중복 없음 — 모두 신규
AI = [
 # ① Anthropic + Google + Broadcom 컴퓨트 파트너십 (실제 기사 검증)
 ("앤트로픽, 구글·브로드컴과 3.5GW TPU 컴퓨팅 확대 — 연 매출 300억 달러 돌파",
  "앤트로픽이 구글·브로드컴과 2027년부터 가동되는 차세대 TPU 컴퓨팅 3.5기가와트 규모의 확대 계약을 체결했다. 2025년 10월의 1기가와트 계약을 대폭 늘린 것으로, 대부분 자원은 미국 내에 배치되며 500억 달러 인프라 투자 약속의 연장선이다. 클로드 수요 급증으로 앤트로픽의 연 매출 실행률은 2025년 말 약 90억 달러에서 300억 달러를 넘어섰다.",
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
]

DESIGN = [
 # ① Firefox + JKR 'Kit' 마스코트 (실제 기사 검증)
 ("파이어폭스, 첫 공식 마스코트 'Kit' 공개 — 'More Fire. More Fox.'",
  "파이어폭스가 글로벌 브랜딩 에이전시 JKR과 협업해 첫 공식 마스코트 'Kit'을 선보였다. Kit은 보호 본능과 활기를 지닌 불꽃빛 여우 캐릭터로, 입이 없는 디자인이라 눈과 자세, 꼬리만으로 감정을 표현한다. 슬로건 'More Fire. More Fox.'로 프라이버시·독립성이라는 강점을 선명히 드러내며, AI 생성이 손쉬운 시대에 사람이 직접 디자인했다는 작가성을 강조했다.",
  "Creative Boom", "https://www.creativeboom.com/news/more-fire-more-fox-meet-kit-firefoxs-most-significant-brand-evolution-in-years/"),
 # ② Adobe + Mother Design 아이덴티티 (실제 기사 검증)
 ("어도비, 마더 디자인과 더 대담한 브랜드 정체성 공개",
  "어도비가 마더 디자인(Mother Design)과 협업해 새 브랜드 정체성을 발표했다. 1982년 마바 워녹의 오리지널 로고에 경의를 표한 새 로고타입이 중심으로, 분리돼 있던 'A' 아이콘과 워드마크를 하나로 통합했다. 컬러 팔레트는 블랙·화이트·더 또렷해진 '어도비 레드'로 단순화됐고, 이미지를 담고 강조하는 프레임형 그래픽 장치 '어도비 렌즈'를 도입해 창의성의 관문이라는 역할을 표현했다.",
  "Creative Boom", "https://www.creativeboom.com/news/reshaping-adobes-global-brand-identity-with-mother-design/"),
 # ③ Pentagram × Hiut Denim (실제 기사 검증)
 ("펜타그램, 웨일스 데님 브랜드 '히우트' 리브랜딩 — 공장과 자연을 잇다",
  "펜타그램 파트너 휴 밀러가 웨일스 아베르테이피의 가족 공장에서 청바지를 만드는 프리미엄 데님 브랜드 히우트(Hiut)의 비주얼 아이덴티티를 새로 디자인했다. 공장의 산업적 정신을 담은 워드마크 아래 'Aberteifi'를 더해 지역·유산·모국어에 브랜드를 뿌리내렸다. 직공들이 청바지마다 남긴 실제 서명에서 따온 손글씨 서체 'The Makers Font'로 공업성과 장인정신의 균형을 구현했다.",
  "Creative Boom", "https://www.creativeboom.com/news/pentagrams-hugh-miller-rebrands-hiut-the-welsh-denim-label-bringing-jeans-making-back-to-aberteifi/"),
 # ④ iOS 27 Liquid Glass 개선 (실제 기사 검증)
 ("iOS 27, '리퀴드 글래스' 대대적 개선 — 투명도 슬라이더·또렷한 아이콘",
  "애플이 WWDC 2026에서 iOS 27의 리퀴드 글래스 디자인을 가독성·개인화·아이콘 등 여러 영역에서 폭넓게 개선한다고 밝혔다. 설정에 새로운 투명도 슬라이더가 추가돼 완전 투명부터 짙은 틴트까지 직접 조절할 수 있다. 복잡한 배경을 분산시키고 요소 가장자리를 어둡게 처리해 가독성을 높였으며, 아이콘은 iOS 26의 흐릿함 지적을 반영해 여러 층의 리퀴드 글래스를 아트워크에 통합하는 방식으로 재설계됐다.",
  "MacRumors", "https://www.macrumors.com/2026/06/10/how-liquid-glass-is-changing-in-ios-27/"),
 # ⑤ Canva + Google Gemini (실제 기사 검증)
 ("캔바, 구글 제미나이와 통합 — AI 디자인 생태계 완성",
  "캔바가 구글 I/O 2026에서 제미나이용 커넥티드 앱을 공개하며 클로드·챗GPT·코파일럿에 이어 모든 주요 AI 플랫폼과의 연동을 완성했다. 사용자는 제미나이 대화창에서 '@Canva'를 입력해 디자인을 생성·편집하고, AI가 만든 이미지를 완전히 수정 가능한 캔바 파일로 전환할 수 있다. 앱은 사용자의 브랜드 키트와 직접 연결돼 첫 프롬프트부터 승인된 폰트·색상·비주얼 아이덴티티를 적용한다.",
  "Fast Company", "https://www.fastcompany.com/91545081/canva-gemini-integration"),
 # ⑥ Cannes Glass Lion 쇼트리스트 (실제 기사 검증)
 ("칸 라이언즈 2026, 글래스 라이언 숏리스트 공개",
  "칸 라이언즈 2026이 변화를 위한 사자상으로 불리는 '글래스 라이언' 숏리스트를 발표했다. 대표성·접근성·정체성·평등 등 사회적 변화를 창의적으로 다룬 작품들이 이름을 올렸으며, 남아공 여성살해에 항의하며 대형 관을 활용한 에델만의 '언베리드 캐스킷' 등이 포함됐다. 올해 심사위원단은 UWG의 모니크 넬슨 회장이 이끌며, 제73회 페스티벌은 6월 22~26일 열린다.",
  "Ad Age", "https://adage.com/events-awards/cannes-lions/aa-glass-lions-shortlist-2026/"),
 # ⑦ 2026 일러스트레이션 트렌드 (실제 기사 검증)
 ("2026 일러스트레이션 6대 트렌드 — '인간의 손길'이 답이다",
  "크리에이티브 붐이 AI 시대에 더욱 빛나는 2026년 일러스트레이션 트렌드 여섯 가지를 제시했다. 핸드 프린팅과 판화 등 수작업 공예가 부활하고, 강렬하고 화사한 색채와 유머러스한 손맛이 주목받는다. 업계는 작가의 정체성과 삶의 경험에서 우러난 진정성 있는 내러티브와 다양성을 중시하는 방향으로 움직이며, 사람의 마음과 손으로 빚어낸 작업이 가장 강력한 차별화 요소로 떠올랐다.",
  "Creative Boom", "https://www.creativeboom.com/inspiration/six-surprising-illustration-trends-for-2026/"),
 # ⑧ Canva Imperfect by Design (실제 기사 검증)
 ("캔바, 2026년은 '의도된 불완전함(Imperfect by Design)'의 해",
  "캔바가 2026년 디자인 트렌드 보고서를 통해 올해를 '의도된 불완전함'의 해로 규정했다. 2억 6천만 명 이상의 커뮤니티 검색 데이터와 1,000명 글로벌 설문을 바탕으로, 창작자들이 AI를 거부하는 대신 자기 방식대로 활용하며 인간적 불완전함을 받아들인다는 분석이다. 응답자 80%가 '2026년은 창작 주도권을 되찾는 해'라 답했고, 리얼리티 워프·텍스처 체크 등 열 가지 트렌드가 제시됐다.",
  "Canva", "https://www.canva.com/newsroom/news/design-trends-2026/"),
 # ⑨ 2026 브랜딩 — 마스코트의 귀환 (실제 기사 검증)
 ("2026 브랜딩의 핵심 무기는 로고가 아니다 — 마스코트의 귀환",
  "크리에이티브 블로그(Creative Bloq)가 2026년 브랜딩에서 가장 강력한 도구로 로고가 아닌 '마스코트'를 지목했다. 포화된 디지털 시장에서 마스코트는 로고 하나로는 만들 수 없는 시각적 지름길을 제공하며, 디지털과 물리적 공간을 넘나드는 일관된 브랜드 접점이 된다. 화면과 알고리즘에 둘러싸인 소비자들이 따뜻함과 개성을 갈망하면서, 마스코트는 인간미를 전하는 전략적 자산으로 재평가받고 있다.",
  "Creative Bloq", "https://www.creativebloq.com/design/branding/the-most-important-branding-tool-in-2026-isnt-what-you-think"),
 # ⑩ 성수 팝업 — 한국 (실제 기사 검증)
 ("성수동 팝업의 진화 — 2026년 6월 꼭 가봐야 할 한국 팝업스토어",
  "비짓코리아(VISITKOREA)가 2026년 6월 한국에서 주목할 팝업스토어를 소개하며 성수동을 팝업의 성지로 조명했다. 붉은 벽돌 공장과 창고가 갤러리·콘셉트 스토어로 재탄생한 성수동은 나이키·젠틀몬스터부터 신생 브랜드까지 '트렌드 선도'와 진정성을 상징하는 무대가 됐다. K패션 브랜드 RAIVE와 헬로키티 협업 팝업, 일상의 축하를 테마로 한 '추카' 팝업 등이 체험형 콘텐츠로 방문객을 맞았다.",
  "VISITKOREA", "https://english.visitkorea.or.kr/svc/contents/contentsView.do?menuSn=177&vcontsId=1590328"),
 # ⑪ Creative Boom 탑 일러스트레이터 15인 (실제 기사 검증)
 ("커뮤니티가 직접 뽑은 2026년 주목할 일러스트레이터 15인",
  "크리에이티브 붐이 1,000여 명의 현업 창작자 설문을 바탕으로 '2026년 최고의 일러스트레이터 15인'을 공개했다. 2026 동계올림픽 공식 포스터를 제작한 유코 시미즈, 에르메스·디올·뉴요커와 협업한 카를로타 프라이어, 바르셀로나 기반의 지니 등이 이름을 올렸다. 선정 작가들의 공통점은 AI가 복제할 수 없는 개인적 시점과 손맛, 수년에 걸쳐 다져온 고유한 시각 언어다.",
  "Creative Boom", "https://www.creativeboom.com/inspiration/the-top-15-illustrators-of-2026-as-chosen-by-the-creative-community/"),
 # ⑫ Messy·Meaningful 일러스트레이션 (실제 기사 검증)
 ("어수선하고 의미 있는, 사람이 그린 일러스트 — 2026 최대 트렌드",
  "크리에이티브 블로그가 2026년 일러스트레이션의 가장 큰 흐름으로 'AI의 매끈함에 대한 반격'을 꼽았다. 의도적으로 거칠고 손으로 그린 듯한 표현, 삐뚤빼뚤한 선과 질감이 정교함을 대신하며, 그 불완전함 자체가 정성과 인간의 손길을 증명한다. 소비자들이 한눈에 의미와 진정성을 원하면서, 이런 일러스트는 사진이나 미니멀리즘보다 빠르게 '사람이 만든 것'이라는 신뢰를 전달한다.",
  "Creative Bloq", "https://www.creativebloq.com/art/illustration/messy-meaningful-and-made-by-humans-the-biggest-illustration-trends-for-2026"),
]

MARKETING = [
 # ① Pinterest AI 광고 (실제 기사 검증)
 ("핀터레스트, 칸 2026서 새 AI 광고·개인화 쇼핑 도구 대거 공개",
  "핀터레스트가 칸 라이언즈 2026에서 새로운 AI 광고 도구와 개인화 쇼핑 기능을 대거 공개했다. AI 기반 퍼포먼스 광고 자동화와 구매 의향 데이터를 결합해 광고주가 캠페인을 더 정교하게 최적화할 수 있도록 했다. 월간 5억 명이 넘는 사용자의 시각적 탐색·구매 여정 데이터를 활용해, 영감에서 구매로 이어지는 전환을 강화하는 것이 핵심이다.",
  "Pinterest Newsroom", "https://newsroom.pinterest.com/news/cannes-2026/"),
 # ② Meta 라이브 쇼핑 + 가상 카드 (실제 기사 검증)
 ("Meta, 라이브 쇼핑 광고·가상 카드 결제 확대 — 소셜 커머스 가속",
  "Meta가 라이브 쇼핑 광고와 가상 카드 결제 기능을 확대해 구매 전환을 끌어올린다. 라이브 방송 중 노출되는 상품을 매끄럽게 결제로 연결하는 원클릭 커머스 경험을 강화했다. 크리에이터·브랜드의 라이브 커머스를 광고 인벤토리와 통합해, 발견부터 결제까지의 마찰을 줄이는 것이 목표다.",
  "Search Engine Land", "https://searchengineland.com/meta-expands-live-shopping-ads-and-virtual-card-checkout-to-drive-more-purchases-480532"),
 # ③ Google AI 오버뷰 광고 (실제 기사 검증)
 ("Google AI 오버뷰, 상업·거래성 쿼리에 광고 본격 확대",
  "구글이 AI 오버뷰(AI 요약 답변)에 상업적·거래성 검색 쿼리를 대상으로 광고 노출을 본격적으로 늘리고 있다. 사용자가 구매 의향을 가진 검색에서 AI 답변 안에 광고가 직접 배치되면서, 기존 오가닉 검색 트래픽과 광고 지면 구조가 재편되고 있다. 마케터들은 AI 검색 시대에 맞춰 콘텐츠·입찰 전략을 재정비해야 한다는 분석이 나온다.",
  "Browser Media", "https://browsermedia.agency/blog/google-ai-overviews-ramping-up-for-commercial-transactional-queries/"),
 # ④ 광고 시장 2026 붐 (실제 기사 검증)
 ("2026 광고 시장 역대 최대 붐 — 헐리우드는 뒤처질 위험",
  "할리우드 리포터는 2026년 글로벌 광고 시장이 역대급 호황을 맞고 있지만 헐리우드는 그 흐름에서 소외될 위험이 있다고 분석했다. 광고비가 전통 TV에서 디지털·스트리밍·소셜·AI 플랫폼으로 빠르게 이동하면서, 변화에 늦은 레거시 미디어가 성장 기회를 놓치고 있다는 진단이다. 빅테크와 스트리밍 플랫폼이 광고 성장의 과실을 대부분 가져가는 구도가 굳어지고 있다.",
  "The Hollywood Reporter", "https://www.hollywoodreporter.com/business/business-news/advertising-boom-2026-tv-hollywood-behind-1236462691/"),
 # ⑤ 마케터의 AI 광고 스택 적응 (실제 기사 검증)
 ("마케터들, 광고 스택 속 AI에 익숙해지다 — 실전 도입 가속",
  "애드익스체인저는 마케터들이 광고 운영 스택 전반에 들어온 AI에 점차 익숙해지고 있다고 전했다. 캠페인 기획·타기팅·크리에이티브 생성·측정에 이르기까지 AI가 일상 워크플로에 자리 잡으며, 초기의 경계심이 실전 활용으로 전환되고 있다. AI를 어떻게 통제하고 검증하느냐가 성과를 가르는 핵심 역량으로 부상했다.",
  "AdExchanger", "https://www.adexchanger.com/marketers/marketers-are-getting-used-to-ai-in-the-ad-stack/"),
 # ⑥ 칸 2026 글래스 라이언 (실제 기사 검증)
 ("칸 라이언즈 2026 D-4 — 글래스 라이언 숏리스트 화제",
  "칸 라이언즈 개막을 앞두고 변화를 위한 사자상 '글래스 라이언' 숏리스트가 화제의 중심에 섰다. 대표성·평등·정체성 등 사회적 변화를 다룬 작품들이 선정되며 포용적 마케팅의 경계가 어디까지 확장될 수 있는지 보여줬다. 제73회 페스티벌은 6월 22~26일 열리며, 최종 수상작이 가를 트렌드 방향에 전 세계 마케터의 이목이 집중되고 있다.",
  "Ad Age", "https://adage.com/events-awards/cannes-lions/aa-glass-lions-shortlist-2026/"),
 # ⑦ IAB 디지털 비디오 광고비 (실제 기사 검증)
 ("미 디지털 비디오 광고비 2026년 800억 달러 돌파 전망 — IAB",
  "IAB에 따르면 미국 디지털 비디오 광고 지출이 2026년 800억 달러를 넘어서며 전체 광고 시장보다 20% 빠르게 성장할 전망이다. 커넥티드 TV(CTV)와 소셜·숏폼 비디오가 성장을 견인하며, 광고주들이 예산을 비디오 중심으로 빠르게 재배분하고 있다. AI 자동화와 데이터 기반 타기팅이 결합되며 비디오 광고의 효율과 측정 가능성이 한층 높아지고 있다.",
  "IAB / PR Newswire", "https://www.prnewswire.com/news-releases/us-digital-video-ad-spend-to-surpass-80b-in-2026-growing-20-faster-than-the-total-ad-market-according-to-iab-302762325.html"),
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
