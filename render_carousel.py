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
 ("Claude에 워터마크 도입 — 부정행위 적발 우려도",
  "Anthropic이 EU AI법 투명성 규정에 맞춰 Claude 출력물에 보이지 않는 워터마크를 넣기 시작했다. 일부 이용자는 업무·과제에서 AI 사용이 드러날까 우려하지만, 정직하게 쓴다면 문제될 게 없다는 반응이 우세하다.",
  "TechCrunch", "https://techcrunch.com/2026/08/12/some-claude-users-are-mad-that-anthropics-new-watermarks-will-catch-them-cheating-at-their-jobs-classes/"),
 ("Cognition, 40조원대 밸류에이션 투자 논의",
  "AI 코딩 에이전트 Devin을 만든 Cognition이 400억 달러 기업가치의 신규 투자를 논의 중이다. 3개월 전 260억 달러였던 밸류에이션이 연환산 매출 10억 달러 달성과 함께 급등했고, 메르세데스-벤츠·NASA·골드만삭스를 고객으로 확보했다.",
  "TechCrunch", "https://techcrunch.com/2026/08/12/ai-coding-startup-cognition-reportedly-already-in-talks-to-raise-at-40b-valuation/"),
 ("AI 안전 논쟁 속 원로 3인 \"개방성 지켜야\"",
  "제프리 힌턴, 페이페이 리, 앤드류 응이 Ai4 컨퍼런스에서 소수 빅테크의 AI 독점을 막으려면 개방성이 필요하다고 입을 모았다. 세부 전략엔 이견이 있었지만 경쟁과 분산된 접근성이 사회에 이롭다는 데는 뜻을 같이했다.",
  "TechCrunch", "https://techcrunch.com/2026/08/12/as-ai-safety-concerns-mount-three-pioneers-make-the-case-for-staying-open/"),
 ("OpenAI 후원 Thrive Holdings, 2조원대 조달",
  "전통기업에 AI를 접목하는 PE 운용사 Thrive Holdings가 소프트뱅크 등으로부터 120억 달러 밸류에이션에 20억 달러를 조달했다. 데이터센터·제조·교통 등 인프라 분야의 인허가·규제 대응까지 AI로 자동화하는 신사업에 나선다.",
  "TechCrunch", "https://techcrunch.com/2026/08/12/openai-backed-thrive-holdings-raises-2b-to-bring-ai-to-the-enterprise/"),
 ("Blacksmith, 1년 만에 밸류에이션 10배",
  "AI 생성 코드를 빌드·테스트·검증하는 Blacksmith가 피크 XV 파트너스 주도로 4500만 달러 시리즈B를 유치해 기업가치가 6000만→5.5억 달러로 뛰었다. 고객사도 700곳에서 5000곳 이상으로 늘었다.",
  "TechCrunch", "https://techcrunch.com/2026/08/12/blacksmiths-valuation-jumps-10x-to-550m-as-ai-coding-fuels-software-validation/"),
 ("구글 딥마인드, 수장 교체 — 카부쿠오을루 체제",
  "코라이 카부쿠오을루가 순다르 피차이 직속으로 구글 딥마인드를 이끌게 됐다. 데미스 하사비스는 회장 겸 알파벳 수석과학자로 물러났고, 27년 재직한 제프 딘도 떠나 새 스타트업 'Discovery Loop'를 창업했다.",
  "CNBC", "https://www.cnbc.com/2026/08/12/google-deepmind-koray-kavukcuoglu.html"),
 ("엔비디아, AI 인프라에 5000억 달러 조달 연합",
  "엔비디아가 아폴로·블랙록·블랙스톤·브룩필드·골드만삭스·KKR과 함께 AI 데이터센터·칩 확충에 5000억 달러 이상을 끌어올 금융 플랫폼을 발표했다. 젠슨 황은 \"컴퓨팅이 곧 매출\"이라며 AI 인프라를 도로·발전소급 투자자산으로 자리매김시켰다.",
  "Fortune", "https://fortune.com/2026/08/12/nvidia-private-capital-deal-circular-financing-ai-boom/"),
]

DESIGN = [
 ("Above Water, 베트남발 코코넛 소다 브랜딩",
  "초이초이 크리에이티브가 벤쩨산 코코넛으로 만든 기능성 스파클링 소다 브랜드 'Above Water'의 로고·아이덴티티·패키지를 새로 디자인했다. 서구 중심이 아닌 아시아의 시각에서 출발하는 새로운 음료 언어를 표방한다.",
  "Brand New", "https://www.underconsideration.com/brandnew/archives/new_logo_identity_and_packaging_for_above_water_by_cho_choi_creative.php"),
 ("헬스케어 스타트업 Amperos, \"신뢰 90%·펑크 10%\" 리브랜딩",
  "디자인 스튜디오 Together가 미국 헬스케어 기업 Amperos의 새 아이덴티티를 공개했다. 소프트웨어가 아닌 사람이 채권 회수를 돕는다는 메시지를 담아 실제 담당자의 사진을 브랜드 전면에 내세웠다.",
  "Brand New", "https://www.underconsideration.com/brandnew/archives/new_logo_and_identity_for_amperos_by_together.php"),
 ("독일 리유저블 컵 RECUP, 10주년 리브랜딩",
  "독일 최대 다회용 컵 서비스 RECUP이 함부르크 에이전시 무타보와 함께 10주년 기념 새 브랜드 정체성을 선보였다. '지속가능성'보다 일상 속 테이크아웃 순간에 자연스럽게 녹아드는 이미지에 무게를 실었다.",
  "Brand New", "https://www.underconsideration.com/brandnew/archives/new_logo_and_identity_for_recup_by_mutabor.php"),
 ("50년 베이커리 Great Harvest, \"Rise and Stay True\"",
  "1976년 창업한 미국 베이커리 체인 Great Harvest가 창립 50주년을 맞아 새 로고와 슬로건 'Rise and Stay True'를 공개했다. 매일 매장에서 직접 반죽하는 정통성과 지역산 밀 사용을 다시 강조했다.",
  "Brand New", "https://www.underconsideration.com/brandnew/archives/new_logo_for_great_harvest.php"),
 ("브롬튼 자전거, 'Life Unfolded' 글로벌 아이덴티티",
  "런던 스튜디오 블랙번이 브롬튼 접이식 자전거가 접히고 펼쳐지는 움직임에서 착안한 새 글로벌 아이덴티티 'Life Unfolded'를 만들었다. 브랜드 고유의 '클라인 블루'와 전용 서체, 속도에 따라 달라지는 모션 시스템이 특징이다.",
  "Brand New", "https://www.underconsideration.com/brandnew/archives/new_identity_for_brompton_bikes_by_studio_blackburn.php"),
 ("종이를 적시고 구겨서 만든 애니메이션",
  "바르셀로나 기반 아티스트 알렉시아 폰세가 종이를 적시고 구기거나 퍼즐 조각 위에서 움직이고 햇빛으로 인화하는 독특한 방식으로 프레임 단위 애니메이션을 만든다. 질감과 불완전함을 살려 유년기의 향수와 상상력을 담아낸다.",
  "It's Nice That", "https://www.itsnicethat.com/articles/alexia-ponce-animation-discover-120826"),
 ("2미터짜리 책 한 권에 담은 인생 아카이브",
  "그래픽 디자이너 앰버 피옹룩이 리소그래프로 인쇄한 길이 2미터짜리 책 'Find The Earth'로 개인의 기억과 사물을 '기억한 것·잊혀가는 것·잊힌 것'으로 분류해 정리했다. 삶 전체를 하나의 시각 아카이브로 압축한 졸업 프로젝트다.",
  "It's Nice That", "https://www.itsnicethat.com/articles/amber-pjongluck-graphic-design-discover-120826"),
]

MARKETING = [
 ("그롱크, 애드리브로 웨어러블 브랜드 광고 출연",
  "회복 지원 웨어러블 브랜드 DNA Vibe가 은퇴 NFL 스타 롭 그롱카우스키를 앞세운 애드리브 캠페인을 시작했다. CEO가 기술을 설명하는 동안 그롱크가 즉흥적으로 농구·저글링을 하며 분위기를 이끈다.",
  "Adweek", "https://www.adweek.com/convergent-tv/watch-gronk-go-off-script-in-new-campaign-for-dna-vibe/"),
 ("버거킹, 4년 만에 웬디스 제치고 2위 탈환",
  "버거킹이 2022년부터 4년간 4억 달러를 투입한 'Reclaim the Flame' 전략으로 브랜딩·메뉴·매장을 일관되게 정비해 2025년 웬디스로부터 미국 2위 버거 체인 자리를 되찾았다. 오락가락하던 과거 캠페인과 달리 꾸준한 실행이 주효했다.",
  "Adweek", "https://www.adweek.com/brand-marketing/how-burger-king-drank-wendys-milk-shake/"),
 ("도브, US오픈 공식 스폰서십 2년차 캠페인",
  "도브가 US오픈 공식 언더암 스폰서 2년째를 맞아 'Don't Sweat It. Make A Racket.' 캠페인을 전개한다. 코미디언 칼리 아퀼리노의 팬 인터뷰 시리즈와 여성 오너 바 10곳 후원으로 스타일과 자신감을 함께 내세운다.",
  "Marketing Dive", "https://www.marketingdive.com/news/doves-marketing-mixes-sports-and-style-for-us-open-sponsorship-return/827652/"),
 ("갭, '옵세션' 스타 앞세운 가을 데님 캠페인",
  "갭이 영화 '옵세션'으로 주목받은 배우 인디 나바레트와 뮤지션 맬컴 토드를 기용한 'Denim on your own' 캠페인을 공개했다. 토드가 로빈의 'Dancing on My Own'을 커버하는 뮤직비디오 형식으로 2026 가을 데님 컬렉션을 알린다.",
  "Marketing Dive", "https://www.marketingdive.com/news/gaps-fall-campaign-enlists-obsession-star-to-promote-denim/827375/"),
 ("어반아웃피터스, 첫 CTV 광고로 캠퍼스 라이프 조명",
  "어반아웃피터스가 러트거스대학교에서 촬영한 첫 CTV 광고 'All Together Now'를 공개했다. 대학생 76명이 등장해 게임데이의 활기를 담았고, 넷플릭스·훌루·유튜브·틱톡에 송출된다.",
  "Marketing Dive", "https://www.marketingdive.com/news/urban-outfitters-embraces-campus-life-in-first-ctv-commercial/827619/"),
 ("세라비, '탠맥싱' 대신 선크림 택하라는 Z세대 캠페인",
  "세라비가 방송인 타일러 캐머런과 피부과 전문의를 내세워 Z세대의 과도한 태닝 트렌드 '탠맥싱'에 맞서는 소셜 시리즈를 시작했다. 태닝베드를 부수는 리모델링 콘셉트로 자외선차단제 사용을 유쾌하게 유도한다.",
  "Marketing Dive", "https://www.marketingdive.com/news/cerave-tackles-tanmaxxing-in-social-series-to-pitch-gen-z-on-spf/827365/"),
 ("월드컵 '음수 타임'이 광고 인벤토리로 재발견",
  "팬들 사이에서 비판받던 월드컵 3분 음수 타임이 뜻밖에 가장 가치 있는 광고 인벤토리로 떠올랐다. 높은 시청 지속률 덕에 광고 패키지에 포함할 만한 상품으로 재평가받고 있다.",
  "Adweek", "https://www.adweek.com/convergent-tv/for-advertisers-hydration-breaks-were-the-star-of-the-world-cup/"),
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
