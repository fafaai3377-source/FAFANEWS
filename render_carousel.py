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
    "옵티무브": "Optimove",
    "쇼핑": "online shopping",
    "마테크": "MarTech",
    "브레이크스루": "breakthrough",
    "어워드": "awards trophy",
    "원더킨드": "Wunderkind",
    "코디얼": "Cordial",
    "펍매틱": "PubMatic",
    "텍사스텍": "Texas Tech",
    "통이완샹": "Tongyi",
    "맥쿼리": "Macquarie",
    "테세우스": "Theseus data center",
    "안드로이드": "Android",
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
 ("앤트로픽, 맥쿼리·GIC와 '테세우스' 데이터센터 합작 출범",
  "앤트로픽이 맥쿼리 자산운용, GIC와 함께 대규모 AI 데이터센터 인프라를 짓는 합작법인 테세우스를 발족했다. 두 투자사가 프로젝트 지분 대부분을 자금 조달하며, 앤트로픽은 전력망 업그레이드 비용 전액과 인근 지역 전기요금 인상분을 부담하기로 했다.",
  "DataCenter Dynamics", "https://www.datacenterdynamics.com/en/news/gic-and-macquarie-form-theseus-infrastructure-to-serve-anthropics-data-center-needs/"),
 ("EU, 구글에 안드로이드를 챗GPT·클로드에 개방하라 명령",
  "유럽연합이 디지털시장법(DMA)에 따라 구글에 웨이크워드·화면 읽기 등 안드로이드 11개 기능을 경쟁 AI 비서에도 동등하게 열라고 명령했다. 늦어도 2027년 8월까지 적용해야 하며 구글은 항소를 예고했지만 이행 의무는 그대로 유지된다.",
  "TNW", "https://thenextweb.com/news/google-eu-android-gemini-rivals-dma"),
 ("알리바바 통이완샹, 실시간 캐릭터 애니메이션 'Wan-Animate-2' 오픈소스 공개",
  "알리바바 통이완샹팀이 골격 추출 없이 24fps 실시간 스트리밍이 가능한 캐릭터 애니메이션 모델 Wan-Animate-2를 아파치 2.0 라이선스로 공개했다. 손동작·미세 표정까지 보존하며 바이트댄스·콰이서우의 상용 서비스와 맞먹는 품질을 보였다.",
  "Pandaily", "https://pandaily.com/tongyi-wan-animate-2-character-animation-open-source-aug2026"),
 ("중국 AgiBot, 유니트리 제치고 세계 1위 휴머노이드 로봇 업체로",
  "상하이 기반 AgiBot이 2026년 상반기 약 8400대를 출하하며 세계 시장점유율 44%로 1위에 올랐다. 유니트리는 5900대·31%로 2위로 밀렸고, 전 세계 휴머노이드 로봇 출하량은 전년 대비 272% 급증했다.",
  "South China Morning Post", "https://www.scmp.com/tech/tech-trends/article/3363544/agibot-overtakes-unitree-top-global-humanoid-robot-vendor-first-half-amid-ipo-push"),
 ("오픈AI, 무료 이용자에게도 GPT-5.6 루나 무제한 대화 제공",
  "오픈AI가 무료·Go 요금제 이용자의 기본 모델을 GPT-5.6 루나로 전환하고 텍스트 대화 무제한 이용을 지원한다고 밝혔다. 무료 이용자도 더 오래 추론하는 '생각하기' 버튼을 메시지별로 쓸 수 있게 됐다.",
  "MacRumors", "https://www.macrumors.com/2026/08/06/chatgpt-free-unlimited-text-chats/"),
 ("앤트로픽 기업가치, 오픈AI 제치고 9650억 달러로",
  "클로드 코드의 성공에 힘입어 앤트로픽의 투자 후 기업가치가 9650억 달러로 평가되며 오픈AI의 3월 말 평가액 8520억 달러를 넘어섰다. 월스트리트저널은 오픈AI가 소비자용 챗봇에 집중하는 사이 개발자용 B2B 시장을 앤트로픽에 내줬다고 짚었다.",
  "파이낸셜뉴스", "https://www.fnnews.com/news/202608021326063966"),
 ("구글, AI 에이전트가 매장에 전화해 재고 확인·구매까지",
  "구글이 이용자를 대신해 매장에 전화를 걸어 재고와 가격을 확인하고 결과를 문자·이메일로 요약해주는 에이전트 기능을 선보였다. 목표가에 도달하면 구글페이로 자동 결제하는 '에이전틱 체크아웃'도 함께 확대되고 있다.",
  "TechBuzz.ai", "https://www.techbuzz.ai/articles/google-launches-ai-agents-to-shop-call-stores-for-you"),
]

DESIGN = [
 ("7UP, 15년 만에 최대 규모 리브랜딩 — '라임' 전면에",
  "케이크 닥터페퍼 산하 7UP이 세로형 신규 로고와 선명한 컬러, '라임 레몬' 표기를 앞세운 새 비주얼 아이덴티티를 공개했다. Z세대·알파세대의 72%가 시트러스 향을 선호한다는 자체 조사를 반영해 레시피도 라임 중심으로 재조정했다.",
  "CNN Business", "https://www.cnn.com/2026/08/10/food/7up-new-recipe-logo"),
 ("어도비, 크리에이티브 에이전트를 일러스트레이터·포토샵 전반으로 확장",
  "어도비가 파이어플라이 기반 크리에이티브 에이전트를 일러스트레이터·포토샵·프리미어 등 크리에이티브 클라우드 전 앱으로 확장했다. 프롬프트만으로 편집 가능한 벡터 도형을 캔버스 안에서 바로 생성할 수 있다.",
  "Adobe News", "https://news.adobe.com/news/2026/06/adobe-unveils-major-expansion"),
 ("텍사스텍, 애슬레틱스 상징 '더블 T' 로고 새단장",
  "텍사스텍이 메탈릭 스타일에서 미니멀한 레드·화이트 톤으로 더블 T 로고를 전면 리뉴얼했다. 2026-27 시즌부터 전 종목 유니폼에 적용되며 아디다스와 함께 리테일 컬렉션도 순차 출시한다.",
  "Dallas Express", "https://dallasexpress.com/sports/texas-tech-athletics-unveils-modernized-logo-from-metallic-to-minimal/"),
 ("피그마, 캔버스에 상주하는 '디자인 에이전트' 정식 도입",
  "피그마가 디자인 작업 화면에서 바로 반복 작업을 자동화하는 전용 AI 에이전트를 공개했다. MCP로 외부 툴과 연결해 컨텍스트를 가져오고, 디자인 시스템 규칙을 준수하며 결과물을 되돌려 쓸 수 있다.",
  "Figma Blog", "https://www.figma.com/blog/the-figma-agent-is-here/"),
 ("BBH, 44년 만의 첫 대규모 리브랜드",
  "광고 에이전시 BBH가 44년 만에 첫 비주얼 아이덴티티 개편을 단행했다. Studio DRAMA와 협업한 전용 서체로 'AI 획일화'에 반기를 들며 창업자들의 개성을 타이포에 녹였다.",
  "It's Nice That", "https://www.itsnicethat.com/articles/bbh-studio-drama-rebrand-graphic-design-project-260226"),
 ("펩시코, 25년 만의 새 비주얼 아이덴티티",
  "펩시코가 약 25년 만에 기업 아이덴티티를 전면 개편했다. 소문자 워드마크와 흙빛 컬러 팔레트, 단순화된 아이코노그래피를 도입하고 'P'를 중심에 뒀다.",
  "PepsiCo", "https://www.pepsico.com/newsroom/stories/2026/an-inside-look-at-pepsico-new-visual-identity-with-its-lead-designer"),
 ("구글 워크스페이스 아이콘, 'AI 퍼스트' 미학으로 대개편 정황",
  "수년간 이어진 플랫·4색 아이콘 체계를 벗어나 제미나이 계열과 결을 맞춘 구글 워크스페이스 신규 아이콘이 유출됐다. 지메일·드라이브 등 핵심 앱 전반에 걸친 대규모 리디자인으로 추정된다.",
  "CGfrog", "https://blog.cgfrog.com/new-google-logos-icons-leaked-2026/"),
]

MARKETING = [
 ("GIGR, 퍼포먼스 마케팅 자동화 'Playad 오토파일럿' 전 세계 출시",
  "샌프란시스코 스타트업 GIGR이 경쟁사 분석부터 소재 제작, 캠페인 세팅까지 한 워크플로에서 처리하는 멀티에이전트 시스템을 정식 출시했다. 소프트론칭 기간 6000개 이상 계정이 가입했고, 한 고객은 운영 시간을 주 20~40시간에서 1시간으로 줄였다고 밝혔다.",
  "PR Newswire", "https://www.prnewswire.com/news-releases/gigr-launches-playad-autopilot-to-automate-end-to-end-performance-marketing-302842490.html"),
 ("펍매틱, 에이전틱 광고 위한 '가드레일' 거버넌스 체계 출시",
  "펍매틱이 자율 광고 거래를 승인 워크플로·감사 추적으로 통제하는 AgenticOS 가드레일 아키텍처를 공개했다. AgenticOS는 올해 1월 출범 이후 10만 개 이상의 웹·앱·스트리밍 서비스에서 80건 넘는 자율 캠페인을 지원해왔다.",
  "PubMatic", "https://pubmatic.com/news/pubmatic-launches-agenticos-the-operating-system-for-agent-to-agent-advertising/"),
 ("오픈AI, 챗GPT 광고에 전환 최적화 입찰 기능 도입",
  "오픈AI가 챗GPT 광고에 전환 목표 기반 입찰(oCPC)과 지역 제외, 대량 캠페인 API 등을 새로 추가했다. 모바일 측정 파트너와 연동해 앱 설치·인앱 이벤트 추적도 지원하며 구글 애즈에 가까운 기능 구성을 갖췄다.",
  "PPC Land", "https://ppc.land/openais-chatgpt-ads-are-getting-conversion-optimization-heres-what-changes/"),
 ("원더킨드-코디얼, 자율 마케팅·전채널 고객 참여로 손잡다",
  "아이덴티티 그래프 기업 원더킨드가 AI 네이티브 CDP 코디얼과 파트너십을 맺고 익명 방문자까지 실시간으로 개인화 메시지를 트리거하도록 통합했다. 장바구니 이탈·재입고·가격 인하 등 행동 신호를 기반으로 이메일·문자를 자동 발송한다.",
  "Morningstar", "https://www.morningstar.com/news/business-wire/20260806184060/wunderkind-partners-with-cordial-to-integrate-autonomous-marketing-and-cross-channel-customer-engagement"),
 ("2026 마테크 브레이크스루 어워드, 제타글로벌·페리온 등 선정",
  "9회째를 맞은 마테크 브레이크스루 어워드가 에이전틱 AI·자율 마케팅 부문 신설과 함께 수상작을 발표했다. 제타글로벌이 '올해의 에이전틱 AI 마케팅 플랫폼', 페리온이 '올해의 자율 캠페인 최적화 솔루션'에 선정됐다.",
  "MarTech Breakthrough", "https://martechbreakthrough.com/2026-winners/"),
 ("팬그램 조사 \"링크드인 장문 게시물 41%가 AI 작성\"",
  "AI 판별 스타트업 팬그램이 5개 플랫폼 100만 건을 분석한 결과 링크드인 장문 게시물의 41%, 단문의 30%가 AI로 작성된 것으로 나타났다. 전체 게시물 중 링크드인 비중은 3분의 1에 불과했지만 AI 콘텐츠 탐지 건수의 3분의 2를 차지했다.",
  "PYMNTS", "https://www.pymnts.com/news/artificial-intelligence/2026/linkedin-feed-is-41percent-fake-cleanup-starts-now/"),
 ("구글 마케팅 라이브 2026, 광고 전반에 AI 대전환 예고",
  "구글이 연례 마케팅 라이브에서 검색·유튜브 전반에 걸친 AI 기반 광고 솔루션을 대거 공개했다. 마케터의 캠페인 기획부터 성과 분석까지 자동화하는 도구들이 핵심으로 소개됐다.",
  "Google", "https://blog.google/products/ads-commerce/google-marketing-live-2026-collection/"),
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
