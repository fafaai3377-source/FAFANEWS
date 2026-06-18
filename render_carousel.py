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
 # ① Anthropic + Google + Broadcom 컴퓨트 파트너십
 ("Anthropic·Google·Broadcom 3.5GW 컴퓨트 동맹 — 매출 30조 돌파",
  "Anthropic이 Google, Broadcom과 손잡고 3.5GW 규모의 AI 전용 컴퓨트 인프라 파트너십을 체결했다. 회사 연간 매출이 30조 원(약 220억 달러)을 돌파하며 클로드 기반 엔터프라이즈 수요가 폭발적으로 성장하고 있다. 자체 칩 개발·클라우드 멀티파트너 전략으로 OpenAI·Microsoft 연합에 정면 도전하는 구도가 본격화됐다.",
  "Anthropic", "https://www.anthropic.com/news"),
 # ② Odyssey $310M 시리즈B
 ("Odyssey $310M 시리즈B — 물리 AI 인프라 빌더 급부상",
  "물리 AI 인프라 스타트업 Odyssey가 6월 17일 3억 1000만 달러 시리즈B 투자를 유치했다. 현실 세계의 물리 법칙을 시뮬레이션하는 AI 엔진을 제공하며, 로보틱스·자율주행·스마트 팩토리 훈련 데이터 생성에 활용된다. 2026년 '물리 AI' 투자 붐의 핵심 플레이어로 부상하며 10억 달러 밸류에이션에 도달했다.",
  "TechStartups", "https://techstartups.com/2026/06/17/odyssey-raises-310-million-series-b/"),
 # ③ Block Managerbot — Jack Dorsey
 ("Jack Dorsey의 Block, AI 에이전트 'Managerbot' 공개",
  "Jack Dorsey가 이끄는 핀테크 기업 Block이 내부 경영 업무를 자율 처리하는 AI 에이전트 'Managerbot'을 공개했다. 채용·성과 리뷰·예산 배분 등 전통적 관리자 역할을 AI가 대체하는 실험적 시도로, Square·Cash App 조직에 시범 적용 중이다. '매니저 없는 회사'를 지향하는 Dorsey의 탈중앙 경영 철학을 AI로 구현한 사례로 업계의 주목을 받고 있다.",
  "VentureBeat", "https://venturebeat.com/ai/jack-dorseys-block-launches-managerbot/"),
 # ④ $1,500 파운데이션 모델 훈련
 ("파운데이션 모델 $1,500에 훈련 성공 — AI 민주화 전환점",
  "연구팀이 단 1,500달러(약 200만 원)의 컴퓨팅 비용으로 경쟁력 있는 파운데이션 모델을 훈련했다고 발표했다. GPT-4급 모델을 수십억 달러로 훈련하던 시대에서 수천 달러대로 극적으로 낮아진 것으로, AI 접근성 혁명을 예고한다. 오픈소스 생태계와 소형 스타트업에게 게임 체인저가 될 수 있다는 전망이 쏟아지고 있다.",
  "VentureBeat", "https://venturebeat.com/ai/researchers-claim-foundation-model-trained-for-1500/"),
 # ⑤ 멀티에이전트 한계 연구
 ("AI 에이전트들 '소통은 되지만 공동 사고는 불가' — 멀티에이전트 한계",
  "최신 연구에 따르면 여러 AI 에이전트가 메시지를 주고받을 수 있어도 공동으로 추론하는 능력은 현저히 부족하다는 결론이 나왔다. 에이전트 간 협업 시 오류 전파·환각 증폭 문제가 단일 에이전트보다 오히려 악화되는 경우가 관찰됐다. 멀티에이전트 AI 시스템을 기업에 도입할 때 '협업 환상'에 주의해야 한다는 경고다.",
  "VentureBeat", "https://venturebeat.com/ai/multi-agent-ai-systems-limits-communication-not-cognition/"),
 # ⑥ Behavox $175M — AI 금융 컴플라이언스
 ("Behavox $175M 유치 — AI 금융 컴플라이언스 100+ 금융사 확보",
  "AI 기반 금융 컴플라이언스 플랫폼 Behavox가 1억 7500만 달러 투자를 유치하며 글로벌 100개 이상 금융기관을 고객으로 확보했다. 임직원 커뮤니케이션·거래 패턴을 AI로 분석해 내부자 거래·자금 세탁 등 금융 범죄를 사전 탐지한다. 금융 규제 강화 흐름 속에서 RegTech AI 시장이 급성장하는 것을 보여주는 투자다.",
  "TechStartups", "https://techstartups.com/2026/06/17/behavox-raises-175-million/"),
 # ⑦ StrictlyVC LA
 ("StrictlyVC LA 오늘 개최 — 방산 테크·물리 AI 집중 조명",
  "TechCrunch의 프리미엄 VC 네트워킹 행사 StrictlyVC가 오늘(6월 18일) 로스앤젤레스에서 개최됐다. 올해는 방산 테크(DefenseTech)와 물리 AI가 핵심 주제로 다뤄지며, 실리콘밸리 상위 VC와 창업자 300명이 참석한다. 2026년 VC 투자 관심사가 소프트웨어에서 하드웨어·물리 세계로 이동하는 흐름을 반영한 프로그램 구성이다.",
  "TechCrunch", "https://techcrunch.com/events/strictlyvc-los-angeles-2026/"),
]

DESIGN = [
 # ① Firefox + JKR 'Kit' 마스코트
 ("Firefox × JKR 'Kit' — 여우에서 마스코트 캐릭터 시대로",
  "Mozilla Firefox가 글로벌 브랜드 에이전시 JKR과 협업해 새 마스코트 캐릭터 'Kit'을 공개했다. 기존 불꽃 여우 로고를 유지하면서 친근하고 개성 있는 캐릭터를 추가해 젊은 사용자층과의 감성적 연결을 강화한다. 브라우저가 단순 도구를 넘어 아이덴티티와 커뮤니티를 갖춘 브랜드로 진화하는 새 챕터를 열었다.",
  "Creative Boom", "https://www.creativeboom.com/news/firefox-jkr-kit-mascot-rebrand/"),
 # ② Adobe + Mother Design 아이덴티티
 ("Adobe, Mother Design과 브랜드 아이덴티티 대폭 리뉴얼",
  "Adobe가 글로벌 브랜드 컨설팅사 Mother Design과 협업해 기업 아이덴티티 시스템을 대대적으로 업데이트했다. AI 중심 크리에이티브 플랫폼으로의 전환을 시각적으로 표현하는 새로운 디자인 언어가 적용됐다. Firefly·Photoshop·Illustrator 등 전 제품군에 걸쳐 통합된 비주얼 경험을 제공한다.",
  "Creative Boom", "https://www.creativeboom.com/news/adobe-mother-design-identity-renewal/"),
 # ③ Pentagram × Hiut Denim
 ("Pentagram × Hiut Denim — 웨일스 장인 데님 브랜드 리브랜딩",
  "글로벌 디자인 스튜디오 Pentagram이 웨일스 소도시 카디건의 장인 데님 브랜드 Hiut Denim의 리브랜딩을 완성했다. '마을 전체가 청바지를 만든다'는 커뮤니티 스토리를 핵심으로, 타이포그래피 중심의 강렬한 아이덴티티를 구현했다. 거대 패션 그룹의 마케팅 공세 속에서 로컬 장인 정신과 진정성이 오히려 차별점이 되는 브랜딩 교과서 사례다.",
  "Creative Boom", "https://www.creativeboom.com/news/pentagram-hiut-denim-rebrand/"),
 # ④ iOS 27 Liquid Glass 개선
 ("iOS 27 Liquid Glass 개선 — Apple 디자인 언어 진화 현황",
  "Apple이 iOS 27 베타에서 Liquid Glass 효과를 지속적으로 개선하고 있다. 초기 베타에서 지적됐던 과도한 반투명과 가독성 문제가 최신 빌드에서 조정됐으며, 개발자들은 새로운 Material API로 앱에 Liquid Glass를 적용할 수 있다. WWDC 2026에서 공개된 이 디자인 언어는 iPadOS·macOS·visionOS에도 확대 적용될 예정이다.",
  "MacRumors", "https://www.macrumors.com/guide/ios-27-liquid-glass/"),
 # ⑤ Canva + Google Gemini
 ("Canva, Google Gemini 통합 — 4대 AI 플랫폼 정식 입점",
  "Canva가 Google Gemini와의 통합을 완료하며 OpenAI·Anthropic·Meta에 이어 4대 AI 플랫폼 모두에 입점하는 최초의 크리에이티브 툴이 됐다. Gemini를 통해 Google Workspace 사용자가 Docs·Slides에서 바로 Canva 디자인을 생성·편집할 수 있다. AI 크리에이티브 툴 시장에서 플랫폼 파트너십이 곧 경쟁력인 시대가 됐다.",
  "Fast Company", "https://www.fastcompany.com/design"),
 # ⑥ Cannes Glass Lion 쇼트리스트
 ("칸 Glass Lion 쇼트리스트 17편 — 젠더·평등 캠페인 정수",
  "칸 라이언즈 2026 Glass Lion(성평등 부문) 쇼트리스트 17편이 발표됐다. 젠더 고정관념 타파·여성 경제적 자립·LGBTQ+ 포용을 주제로 한 글로벌 캠페인들이 선정됐다. 수상작은 6월 22~26일 칸 현장에서 발표되며, 사회적 메시지를 크리에이티브 파워로 전달하는 방법론의 교과서가 될 것이다.",
  "Roastbrief", "https://roastbrief.us/cannes-lions-2026-glass-lion-shortlist/"),
 # ⑦ 2026 일러스트레이션 트렌드
 ("2026 일러스트레이션 6대 서프라이징 트렌드",
  "Creative Boom이 선정한 2026년 일러스트레이션 업계의 6대 '서프라이징 트렌드'가 발표됐다. AI 도구 활용의 일상화, 손 그림 감성의 반작용적 부활, 텍스처와 혼합 미디어의 귀환, 내러티브 중심 시리즈 작업 증가 등이 핵심이다. 디지털과 아날로그가 공존하는 '포스트 AI 일러스트레이션' 시대가 열리고 있다.",
  "Creative Boom", "https://www.creativeboom.com/insight/illustration-trends-2026/"),
 # ⑧ Canva Imperfect by Design
 ("Canva 'Imperfect by Design' — 불완전함이 2026 핵심 비주얼 트렌드",
  "Canva가 발표한 2026 비주얼 트렌드 리포트에서 'Imperfect by Design(의도적 불완전함)'이 핵심 키워드로 선정됐다. AI 생성 이미지의 과잉 완벽성에 대한 반작용으로, 흔들린 사진·손글씨·거친 질감이 오히려 진정성과 인간성을 전달한다. 브랜드들이 의도적으로 '덜 완성된 듯한' 비주얼을 선택하는 전략적 흐름이 확산되고 있다.",
  "Canva", "https://www.canva.com/learn/design-trends-2026/"),
 # ⑨ 브랜딩 스토리텔링
 ("브랜딩의 가장 강력한 무기는 스토리텔링 — 2026 실전 가이드",
  "Creative Bloq이 2026년 브랜딩 환경에서 스토리텔링이 가장 강력한 차별화 도구임을 분석한 심층 가이드를 발행했다. 창업 신화·실패 경험·커뮤니티 참여를 브랜드 서사로 엮는 구체적 방법론을 제시한다. AI 생성 콘텐츠가 범람하는 시대에 '진짜 이야기'가 소비자 신뢰와 충성도의 핵심 원천이 되고 있다.",
  "Creative Bloq", "https://www.creativebloq.com/features/branding-storytelling-guide-2026"),
 # ⑩ 성수 팝업 — 한국
 ("성수 6월 팝업 21곳 동시 운영 — 겐조·아디다스·로컬 브랜드",
  "서울 성수동에 이번 달 동시에 운영 중인 팝업 스토어가 21곳에 달하며 브랜드 마케팅 성지로서의 입지를 굳히고 있다. 겐조·아디다스 등 글로벌 브랜드와 한국 로컬 브랜드가 뒤섞여 독특한 공간 경험을 경쟁하고 있다. 팝업 밀도가 높아질수록 소비자 피로도 vs 시너지 효과에 대한 업계 토론도 활발해지고 있다.",
  "Popga", "https://popga.co.kr/seongsu-june-2026-popups"),
 # ⑪ Creative Boom 탑 일러스트레이터 15인
 ("Creative Boom 선정 지금 주목할 일러스트레이터 15인",
  "Creative Boom이 2026년 현재 가장 주목받는 일러스트레이터 15인을 선정했다. 디지털·전통 미디어를 넘나드는 다양한 작가들이 포함됐으며, 각자의 독보적 시각 언어로 글로벌 브랜드와 출판 업계에서 활발하게 활동 중이다. 일러스트레이션을 통한 브랜드 아이덴티티 구축 사례로도 주목받고 있다.",
  "Creative Boom", "https://www.creativeboom.com/inspiration/illustrators-to-watch-2026/"),
 # ⑫ Messy·Meaningful 일러스트레이션
 ("'Messy & Meaningful' — 지저분함이 곧 의미가 되는 일러스트레이션 반란",
  "Creative Bloq이 '지저분하고 의미 있는(Messy & Meaningful)' 스타일의 일러스트레이션이 2026년 크리에이티브 업계에서 반란을 일으키고 있다고 분석했다. 깔끔한 벡터·AI 생성 이미지의 시대에 역행하는 손으로 그린 듯한 거칠고 불규칙한 스타일이 브랜드와 독자에게 오히려 강한 감성적 연결을 만든다. 개성과 진정성을 갈구하는 시대정신의 반영이다.",
  "Creative Bloq", "https://www.creativebloq.com/illustration/messy-meaningful-illustration-trend-2026"),
]

MARKETING = [
 # ① Pinterest AI 광고 + MCP
 ("Pinterest, AI 광고 스위트 + MCP 서버 출시 — 검색 광고 혁신",
  "Pinterest가 AI 기반 광고 최적화 스위트와 함께 Model Context Protocol(MCP) 서버를 출시했다. 광고주가 AI 에이전트를 통해 Pinterest 광고를 자동 기획·집행할 수 있는 최초의 시각 검색 플랫폼이 됐다. 월간 활성 이용자 5억 명의 구매 의향 데이터를 AI로 분석해 전환율을 높이는 새로운 광고 패러다임을 제시했다.",
  "Pinterest Newsroom", "https://newsroom.pinterest.com/en/post/pinterest-ai-ad-suite-mcp"),
 # ② Meta 라이브 쇼핑 + Mastercard
 ("Meta 라이브 쇼핑 광고 + Mastercard 가상 카드 — 소셜 커머스 가속",
  "Meta가 Instagram·Facebook 라이브 쇼핑 광고에 Mastercard 가상 카드 즉시 결제를 통합했다. 라이브 방송 중 상품을 클릭하면 가상 카드로 1초 안에 결제가 완료되는 원클릭 소셜 커머스 경험을 구현했다. TikTok Shop의 공세에 맞서는 Meta의 반격으로, 크리에이터 이코노미와 광고 수익의 통합이 가속화된다.",
  "Search Engine Land", "https://searchengineland.com/meta-live-shopping-mastercard-virtual-card"),
 # ③ Google AI 오버뷰 광고
 ("Google AI Overviews 광고, 상업 쿼리 18.6% 노출 — 검색 광고 지형 변화",
  "분석 결과 Google AI Overviews(AI 요약 답변)가 상업적 검색 쿼리의 18.6%에 광고를 함께 노출하는 것으로 나타났다. SEO 전문가들 사이에서 '오가닉 검색의 종말'에 대한 논의가 다시 불붙고 있다. AI 답변 내 광고 배치가 정착되면 클릭당 단가와 광고 지면 구조가 근본적으로 재편될 것으로 전망된다.",
  "Sharp Innovations", "https://www.sharpinnovations.com/blog/google-ai-overviews-ads-commercial-queries"),
 # ④ 광고 시장 2026 붐
 ("2026 광고 시장 역대 최대 붐 — Hollywood은 뒤처질 위험",
  "2026년 글로벌 광고 시장이 AI·디지털·스포츠 이벤트 트리플 효과로 역대 최대 성장을 기록하고 있다. 그러나 Hollywood 엔터테인먼트 업계는 스트리밍 광고 전환이 늦어 이 붐에서 소외될 위험이 있다는 분석이 나왔다. 광고주의 예산이 전통 TV에서 디지털·소셜·AI 플랫폼으로 빠르게 이동하는 것이 핵심 이유다.",
  "The Hollywood Reporter", "https://www.hollywoodreporter.com/business/business-news/2026-ad-market-boom-hollywood-left-behind"),
 # ⑤ LLM 광고 캠페인 테스트
 ("마케터들 LLM으로 광고 캠페인 테스트 확산 — 새로운 실험 문화",
  "글로벌 마케터들 사이에서 Claude·GPT-4·Gemini 등 LLM을 활용해 광고 캠페인 콘셉트를 사전 테스트하는 실험이 빠르게 확산되고 있다. 소비자 반응 시뮬레이션, 메시지 A/B 테스트, 크리에이티브 방향 검증에 AI를 활용해 캠페인 실패율을 낮추는 새로운 워크플로가 자리 잡고 있다. '리서치 에이전시'의 역할 자체가 AI로 대체되고 있다는 위기감도 동시에 퍼지고 있다.",
  "Best Media Info", "https://bestmediainfo.com/2026/06/18/marketers-llm-ad-campaign-testing"),
 # ⑥ 칸 2026 D-4
 ("칸 라이언즈 2026 D-4 — Glass Lion 쇼트리스트 화제",
  "칸 라이언즈 축제 개막 4일 전, Glass Lion(성평등) 쇼트리스트가 화제의 중심에 섰다. 젠더 편견 해소와 포용적 마케팅의 경계를 어디까지 확장할 수 있는지 보여주는 17편의 작품들이 업계 토론을 촉발했다. 6월 22일 개막 후 26일까지 이어지는 축제에서 최종 수상작이 가를 트렌드 방향에 전 세계 마케터의 이목이 집중되고 있다.",
  "Cannes Lions", "https://www.canneslions.com/awards/glass-lion"),
 # ⑦ 2026 디지털 광고 3대 성장축
 ("2026 디지털 광고 3대 성장축 — AI·소셜커머스·CTV",
  "Smartly의 2026년 디지털 광고 트렌드 리포트에 따르면 AI 자동화·소셜 커머스·커넥티드 TV(CTV)가 3대 성장 축으로 자리매김했다. AI 기반 크리에이티브 자동화는 광고 제작 비용을 평균 40% 절감했으며, 소셜 커머스 전환율은 전년 대비 2.3배 상승했다. CTV 광고 지출은 2026년 처음으로 전통 TV를 추월할 것으로 예측됐다.",
  "Smartly", "https://smartly.io/blog/digital-advertising-trends-2026"),
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
