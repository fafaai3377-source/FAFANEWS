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
 ("메타, 대형 코드베이스용 AI 에이전트 '뮤즈 코드' 공개",
  "메타가 대규모 코드 저장소에서 복잡한 프로그래밍 작업을 처리하는 AI 에이전트 '뮤즈 코드'를 베타로 출시했다. 자체 코딩 모델 뮤즈 스파크를 기반으로 여러 하위 에이전트에 작업을 병렬로 분산해 처리하며, 오픈AI·앤스로픽 코딩 도구보다 저렴한 대안을 내세운다.",
  "TechCrunch", "https://techcrunch.com/2026/08/05/meta-launches-muse-code-an-ai-agent-for-large-code-bases/"),
 ("제프 딘 등 구글 AI 연구진, 신생기업 '디스커버리 루프' 창업",
  "제프 딘, 산자이 게마와트, 쿼크 레 등 구글의 핵심 AI 연구자들이 퇴사해 과학 연구 자동화를 목표로 하는 공익법인 '디스커버리 루프'를 설립했다. 초기 투자는 래디컬 벤처스와 코슬라 벤처스가 공동 주도했으며 클라이너 퍼킨스, 알파벳 등이 참여했다.",
  "TechCrunch", "https://techcrunch.com/2026/08/05/jeff-dean-and-other-top-ai-researchers-are-leaving-google-to-launch-their-own-startup/"),
 ("AI 스타트업 하크, 웹 작업 대신 처리하는 브라우저 에이전트 공개",
  "시리즈A로 7억 달러를 유치한 스타트업 하크가 API 없이도 웹사이트를 직접 조작해 작업을 수행하는 브라우저 에이전트 '핸드오프'를 공개했다. 타겟, 월마트, 오픈테이블 등에서 음식 주문이나 예약을 자동 처리하며 올여름 정식 출시를 준비 중이다.",
  "TechCrunch", "https://techcrunch.com/2026/08/05/hark-previews-its-browser-use-agent-for-completing-tasks/"),
 ("앤스로픽, 자체 AI 반도체 설계팀 신설",
  "앤스로픽이 자체 AI 칩을 설계하는 내부 팀을 구성하고 있다고 밝혔다. 하드웨어와 모델을 함께 설계해 클로드의 처리 속도와 효율을 높이려는 목적으로, 오픈AI·구글·메타의 자체 칩 개발 흐름을 뒤따른다.",
  "TechCrunch", "https://techcrunch.com/2026/08/05/anthropic-is-hiring-an-ai-chip-design-team/"),
 ("美스타트업, AI 전용 공장 짓기에 2500만달러 시드 유치",
  "미국 스타트업 파운데이셔널 인더스트리스가 AI 네이티브 공장 네트워크 구축을 위해 2500만 달러 규모의 시드 투자를 유치했다. 설계·로봇 공정·품질관리·공급망까지 하나의 AI 시스템으로 조율해 데이터센터 장비 생산부터 시작할 계획이다.",
  "AI Insider", "https://theaiinsider.tech/2026/08/05/foundational-industries-raises-25m-in-seed-funding-to-build-ai-native-factories/"),
 ("美 항소법원, 아마존의 퍼플렉시티 AI 쇼핑 에이전트 금지 뒤집어",
  "미국 제9순회항소법원이 퍼플렉시티의 AI 쇼핑 에이전트 '코멧'에 대한 아마존의 접속 금지 가처분을 무효화했다. 법원은 에이전트가 사용자 지시에 따라 작동하는 만큼 아마존 서버에 접속한 주체는 소프트웨어가 아닌 이용자라고 판단했다.",
  "TNW", "https://thenextweb.com/news/amazon-loses-perplexity-comet-ai-shopping-ruling"),
 ("기상 스타트업 윈드본, AI 예보 확장에 370억원 유치",
  "기상 관측 스타트업 윈드본시스템즈가 코슬라벤처스 주도로 3700만 달러 규모 시리즈B를 유치하며 기업가치 2억5000만 달러를 인정받았다. 전 세계 약 600개 기상 관측 기구의 대기 데이터를 정부 데이터와 결합해 AI 예보 모델을 구동한다.",
  "TechCrunch", "https://techcrunch.com/2026/08/05/ai-makes-weather-prediction-better-can-windborne-make-it-lucrative/"),
]

DESIGN = [
 ("소프트웨어 브랜드 '드라이버', 흑백 아이덴티티로 재단장",
  "디자인 에이전시 Play가 소프트웨어 브랜드 드라이버(Driver)의 새 로고와 아이덴티티를 공개했다. 산세리프 타이포그래피와 흑백 톤을 기반으로 애니메이션 요소를 더해 AI·코드·데이터 중심의 기술 이미지를 강조했다.",
  "Brand New", "https://www.underconsideration.com/brandnew/archives/new_logo_and_identity_for_driver_by_play.php"),
 ("리조트 브랜드 바이아 프린시페, 태양 심볼로 새 단장",
  "디자인 스튜디오 비어드우드가 호스피탤리티 브랜드 바이아 프린시페의 로고와 아이덴티티를 리뉴얼했다. 하이 콘트라스트 산세리프 서체와 태양 모티프를 중심으로 기존 브랜드의 휴양지 이미지를 계승하면서도 현대적으로 다듬었다.",
  "Brand New", "https://www.underconsideration.com/brandnew/archives/new_logo_and_identity_for_bahia_principe_by_beardwood.php"),
 ("벨기에 생수 브랜드 BRU, 사슴 그래픽 담아 패키지 개편",
  "벨기에 디자인 스튜디오 위원트모어가 생수 브랜드 BRU의 로고와 패키지 디자인을 새롭게 선보였다. 파란색 컬러 팔레트와 사슴 일러스트레이션을 활용해 브랜드의 자연친화적 이미지를 강조했다.",
  "Brand New", "https://www.underconsideration.com/brandnew/archives/new_logo_and_packaging_for_bru_by_wewantmore.php"),
 ("파리 스트리트볼 대회 콰이54, 네온 스트라이프로 새 옷",
  "디자이너 알렉산더 와이즈와 360 크리에이티브가 파리 기반 농구 대회 콰이54 2026 에디션의 새 아이덴티티를 공개했다. 네온 컬러와 스트라이프 패턴을 활용해 대회의 스트리트 문화 정체성을 시각적으로 강조했다.",
  "Brand New", "https://www.underconsideration.com/brandnew/archives/new_logo_and_identity_for_quai_54_2026_by_alexander_wise_and_360_creative.php"),
 ("TBWA, 손맛 살린 새 글로벌 아이덴티티 공개",
  "글로벌 광고 네트워크 TBWA가 사내 디자인 조직 DXD와 함께 새로운 글로벌 비주얼 아이덴티티를 공개했다. 세리프 서체와 손으로 그린 모노그램, 잉크 질감의 아이코노그래피로 인간적인 손맛을 강조했으며 빈티지 편지지에서 영감받은 '아날로그' 컬러를 더했다.",
  "Shots", "https://shots.net/news/view/tbwa-makes-its-mark-with-a-handmade-new-identity"),
 ("피그마, 코드 커넥트로 AI 코딩 에이전트 토큰 30% 절감",
  "피그마가 MCP 서버의 코드 커넥트 기능이 AI 코딩 에이전트 성능에 미치는 영향을 분석한 결과를 공개했다. 코드 커넥트를 활성화하면 작업 시간이 19.6%, 토큰 사용량이 29.5% 줄고 코드 품질 점수도 개선됐다.",
  "Figma Blog", "https://www.figma.com/blog/the-benefits-of-code-connect-in-mcp/"),
 ("필라델피아 디자이너 샘 웬크, 손으로 만든 앨범 커버로 주목",
  "필라델피아 기반 아티스트 샘 웬크가 블록 프린트와 아카이벌 소재를 활용한 콜라주 스타일 앨범 커버와 포스터를 독립 음악가들을 위해 제작하고 있다. 독학으로 디자인을 익힌 그는 손으로 작업한 흔적이 대량생산 콘텐츠와의 차별점이라고 말한다.",
  "It's Nice That", "https://www.itsnicethat.com/articles/sam-wenc-lobby-art-editions-illustration-graphic-design-discover-050826"),
]

MARKETING = [
 ("디즈니-틱톡, 크리에이터 앰배서더 프로그램으로 팬덤 콘텐츠 확대",
  "디즈니와 틱톡이 손잡고 '디즈니 크리에이터 앰배서더 프로그램'을 출시했다. 마블, 스타워즈 등 주요 프랜차이즈의 라이선스 자산을 크리에이터에게 개방해 팬 콘텐츠 제작을 지원하며, 영상은 틱톡과 디즈니플러스의 세로형 영상 기능에 동시 노출된다.",
  "Marketing Dive", "https://www.marketingdive.com/news/disney-tiktok-partner-on-content-sharing-as-creators-fuel-fandom/827054/"),
 ("\"차 한 잔의 시간\", 인도 마케팅 새 채널로 주목",
  "BN그룹 CMO 키란 기라드카르는 인도의 '짜이타임'이 신뢰도와 빈도가 높은 미개척 마케팅 채널이라고 강조했다. 노상 찻집 시음, 맥락형 패키지 문구, 컵·주전자 등 짜이 생태계 브랜딩으로 일상 속 자연스러운 브랜드 노출을 만들 수 있다고 제안했다.",
  "BestMediaInfo", "https://bestmediainfo.com/mediainfo/mediainfo-marketing/chai-time-could-be-indias-most-underutilised-media-channel-says-kiran-giradkar-bn-group-12230124"),
 ("스펙세이버스, 그루브 아르마다와 '레이브 세대' 청력검사 캠페인",
  "안경·보청기 브랜드 스펙세이버스가 1990년대 레이브 세대인 50대 이상을 겨냥해 '더 테스트리스트' 캠페인을 시작했다. 무료 온라인 청력 스크리닝을 완료하면 10월 런던 그루브 아르마다 단독 공연 등 한정 라이브 이벤트에 참석할 기회를 준다.",
  "Retail Gazette", "https://www.retailgazette.co.uk/blog/2026/08/specsavers-club-guestlist/"),
 ("핀터레스트, AI 쇼핑 전환 승부수...2분기 매출 18% 성장",
  "핀터레스트가 '영감' 중심 플랫폼에서 'AI 쇼핑' 플랫폼으로 전환을 가속화하고 있다. 핀터레스트 어시스턴트, 퍼포먼스+ 등 AI 도구를 앞세운 결과 2분기 매출은 전년 대비 18% 증가한 11억8000만달러, 월간 활성 사용자는 6억4000만명을 기록했다.",
  "BestMediaInfo", "https://bestmediainfo.com/mediainfo/mediainfo-digital/pinterests-q2-2026-results-put-its-ai-led-shopping-pivot-to-the-test-12230222"),
 ("\"당신 회사의 인텐트 데이터, 경쟁사에 팔리고 있다\"",
  "마테크 매체 MarTech이 소프트웨어 벤더들이 필요 이상의 고객 데이터를 수집해 상업적 제품에 활용하고 있다는 연구 결과를 보도했다. 조사 대상 700여개 벤더 중 다수에서 컴플라이언스 감사 시 추적을 중단하는 코드가 발견됐다.",
  "MarTech", "https://martech.org/is-your-intent-data-being-sold-to-your-competitors/"),
 ("AI 시대, 마케터의 가치는 '전문성'에서 '팀 육성 능력'으로",
  "MarTech 칼럼니스트 캐슬린 쇼브는 AI 확산으로 개인의 실무 전문성 가치가 낮아지는 대신 판단력과 방향 제시, 후배 육성 능력이 더 중요해지고 있다고 분석했다. 유능한 마케터는 '가장 많이 아는 사람'이 아니라 팀을 더 나은 의사결정으로 이끄는 사람이어야 한다고 강조한다.",
  "MarTech", "https://martech.org/the-future-belongs-to-marketers-who-make-others-smarter/"),
 ("AI 검색 시대, 쇼핑몰이 준비해야 할 콘텐츠 전략은",
  "AI 기반 검색이 확산되며 소비자의 쇼핑 탐색 방식이 키워드 검색에서 대화형 질의로 바뀌고 있다. 모비인사이드는 쇼핑몰이 AI 검색 결과에 노출되려면 상품 정보 구조 개편과 구매 관련 질문에 답하는 콘텐츠 제작, 데이터 신선도 관리가 필요하다고 조언했다.",
  "모비인사이드", "https://www.mobiinside.co.kr/2026/08/05/ai-search-shoppingmall-seo-strategy/"),
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
    # og:image가 기사 내용과 무관한 것으로 확인된 URL은 항상 제목 기반 검색 강제
    FORCE_SEARCH_URLS = {
        "https://techcrunch.com/2026/08/05/anthropic-is-hiring-an-ai-chip-design-team/",
        "https://theaiinsider.tech/2026/08/05/foundational-industries-raises-25m-in-seed-funding-to-build-ai-native-factories/",
    }
    def _force(url):
        if url in FORCE_SEARCH_URLS: return True
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
