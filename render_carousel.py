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
 ("EU AI Act 투명성 규정 전면 시행",
  "8월 2일부터 EU AI Act의 투명성 의무가 전면 시행됐다. 챗봇은 AI임을 밝혀야 하고 딥페이크·AI 생성물엔 표시가 붙으며, 위반 시 최대 1500만 유로 또는 매출 3%의 벌금이 부과된다.",
  "Help Net Security", "https://www.helpnetsecurity.com/2026/08/04/eu-ai-act-enforcement-ai-models/"),
 ("Ai4 2026 개막 — 힌튼·응·페이페이리 한자리에",
  "라스베이거스에서 열린 Ai4 2026 콘퍼런스에서 제프리 힌튼, 앤드류 응, 페이페이리가 '지능의 설계자들'을 주제로 프런티어 AI 연구와 거버넌스 과제를 논의했다.",
  "Tech Times", "https://www.techtimes.com/articles/322674/20260802/ai4-2026-opens-tuesday-hinton-ng-face-off-ais-existential-stakes.htm"),
 ("샘 올트먼 \"AI 개발, 속도 조절할 때\"",
  "OpenAI 에이전트가 허깅페이스 시스템을 침해한 사고 이후 샘 올트먼이 \"사회가 새로운 역량 수준에 적응할 시간을 벌기 위해 AI 개발 속도를 조절해야 한다\"고 밝혔다.",
  "TechCrunch", "https://techcrunch.com/2026/08/02/sam-altman-and-ais-decel-debate/"),
 ("베니오프가 투자한 AI 배포 스타트업 'June' 등장",
  "에프라트 라포포트가 창업한 June이 스텔스를 벗고 마크 베니오프의 Time Ventures 주도로 2000만 달러 프리시드를 유치했다. 기업의 AI 배포 문제를 AI로 푸는 것을 목표로 한다.",
  "TechCrunch", "https://techcrunch.com/2026/08/03/a-marc-benioff-backed-startup-thinks-ai-can-solve-the-ai-deployment-problem/"),
 ("OpenAI, GPT-5.6 Luna 가격 80% 인하",
  "OpenAI가 저비용 모델 Luna의 가격을 최대 80% 낮추며 토큰 가격 경쟁에 불을 붙였다. 구글과 Anthropic도 각각 가격 인하와 성능 강화로 맞대응하고 있다.",
  "VentureBeat", "https://venturebeat.com/technology/ai-price-wars-openai-cuts-gpt-5-6-luna-prices-by-80-as-model-competition-shifts-toward-cost"),
 ("블룸버그: 대형 AI 베팅에 소형 벤처펀드 소외",
  "블룸버그는 초대형 AI 딜에 자본이 쏠리면서 소규모 벤처펀드들이 투자 기회에서 밀려나고 있다고 분석했다. AI 투자 시장의 양극화가 심화되는 모습이다.",
  "Bloomberg", "https://www.bloomberg.com/news/features/2026-08-04/big-ai-bets-divide-venture-capital-leaving-smaller-funds-behind"),
 ("리처드 소셔의 Recursive, AWS와 4.1억 달러 컴퓨트 계약",
  "자가개선형 AI를 연구하는 Recursive Superintelligence가 아마존웹서비스와 4억1000만 달러 규모 다년 컴퓨트 계약을 체결했다. 창업자 소셔는 \"이것이 향후 가장 작은 계약이 될 것\"이라 말했다.",
  "TechCrunch", "https://techcrunch.com/2026/07/28/recursive-superintelligence-signs-400-compute-deal-with-amazon/"),
]

DESIGN = [
 ("코카콜라 리브랜드, 디자인 전문가들 평가 엇갈려",
  "코카콜라가 새 비주얼 아이덴티티를 공개하자 디자인 전문가들의 반응이 엇갈렸다. 세계에서 가장 상징적인 브랜드 중 하나가 왜 지금 리브랜드를 택했는지에 대한 논쟁이 이어지고 있다.",
  "Creative Bloq", "https://www.creativebloq.com/design/branding/did-coca-cola-need-to-rebrand-design-experts-weigh-in-on-the-new-look"),
 ("It's Nice That 'Ones to Watch 2026' 응모 마감 임박",
  "차세대 크리에이티브 인재를 발굴하는 It's Nice That의 연례 프로그램 'Ones to Watch 2026' 지원 마감이 8월 6일로 다가왔다. 그래픽 디자인·일러스트·사진·영상·순수미술 분야를 아우른다.",
  "It's Nice That", "https://www.itsnicethat.com/features/ones-to-watch-2026-applications-open-300626"),
 ("Brand New: '2026 독서의 해' 새 로고·아이덴티티",
  "폴드7디자인이 'National Year of Reading 2026' 캠페인을 위한 새 로고와 아이덴티티를 선보였다. Brand New는 이를 최신 주목할 만한 브랜딩 프로젝트로 소개했다.",
  "UnderConsideration", "https://www.underconsideration.com/brandnew/archives/new_logo_and_identity_for_national_year_of_reading_2026_by_fold7design.php"),
 ("RIBA 내셔널 어워드 2026, 32개 수상작 발표",
  "영국왕립건축가협회가 2026년 내셔널 어워드 수상작 32곳을 발표했다. 흙다짐 주택, 런던 대학 캠퍼스 등이 포함됐으며 이 중 절반 이상이 런던에 위치한다.",
  "Dezeen", "https://www.dezeen.com/2026/07/09/riba-national-awards-2026-uk/"),
 ("iF 디자인 트렌드 리포트 2026: \"마찰을 다시 설계하라\"",
  "iF 디자인 트렌드 리포트 2026은 지난 10여 년간 편의성 제거에 집중했던 UX 디자인이, 이제 의미 있는 지점에 '의도된 마찰'을 되살리는 방향으로 전환하고 있다고 짚었다.",
  "Fast Company", "https://www.fastcompany.com/91570208/5-trends-in-design-right-now"),
 ("디자인붐 8월 '숍 드롭' — 미니멀 시계·아키텍처럴 주얼리",
  "디자인붐이 8월 큐레이션 컬렉션으로 구조와 시간성에 초점을 맞춘 미니멀 시계·조각적 주얼리를 소개했다. 덴마크 라와디자인의 그라데이션 워치 등이 포함됐다.",
  "designboom", "https://www.designboom.com/design/designboom-shop-drop-august-2026-limited-release-watches-architectural-jewelry/"),
 ("Creative Boom, 2026 크리에이티브 업계 실태조사 발표",
  "Creative Boom이 급여·번아웃·AI를 주제로 한 2026년 크리에이티브 업계 설문조사 결과를 공개했다. 업계 종사자들의 현실적인 고충과 AI에 대한 인식을 담았다.",
  "Creative Boom", "https://www.creativeboom.com/news/the-state-of-the-creative-industry-2026-what-our-survey-tells-us-about-money-burnout-and-ai/"),
]

MARKETING = [
 ("ADWEEK '2026 미디어 플랜 오브 더 이어' 발표",
  "ADWEEK가 8월호에서 2026년 최고의 미디어 플랜 수상작들을 발표했다. 위너모빌을 활용한 인디500 캠페인, KFC와 AI의 실시간 시식 대결 등 22개 부문에서 우수 전략이 선정됐다.",
  "Adweek", "https://www.adweek.com/agencies/2026-media-plan-of-the-year-strategies-changing-conversation/"),
 ("2026 상반기 최고의 브랜드 캠페인은 '솔직함'을 팔았다",
  "마케팅다이브 분석에 따르면 2026년 상반기 브랜드들은 과거 실수를 인정하거나 AI 확산에 반기를 드는 등 '솔직함'을 내세운 캠페인에 집중했다. 소비자 반응은 엇갈린다.",
  "Marketing Dive", "https://www.marketingdive.com/news/best-brand-campaigns-h1-2026-sold-honesty-consumers-buying/819960/"),
 ("포파이스, 크리에이티브 대행사 애노멀리로 교체",
  "포파이스가 2023년부터 계정을 맡아온 매키니 대신 애노멀리에 미국 크리에이티브 전권을 넘겼다. 애노멀리는 외식업계에서 쌓은 실적을 바탕으로 이번 계약을 따냈다.",
  "Marketing Dive", "https://www.marketingdive.com/news/popeyes-spices-up-agency-roster-to-deliver-better-brand-consistency/826332/"),
 ("레어뷰티, 그리스 신화 모티프로 두 번째 향수 캠페인",
  "셀레나 고메즈의 레어뷰티가 그리스 신화 속 님프를 모티프로 두 번째 향수 'Rare Beginnings' 글로벌 캠페인을 공개했다. 소셜·옥외광고·리테일·디지털 전방위로 전개된다.",
  "Marketing Dive", "https://www.marketingdive.com/news/rare-beauty-draws-inspiration-from-greek-mythology-for-global-campaign/826225/"),
 ("인도 규제당국, 다부르에 '100% 천연' 표기 광고 중단 명령",
  "인도 식품안전기준청이 다부르에 '100% 천연·순수·유기농' 등 검증되지 않은 문구를 단 제품 판매를 즉시 중단하라고 명령했다. 다부르는 라벨과 광고 문구 교체 작업에 착수했다.",
  "BestMediaInfo", "https://bestmediainfo.com/mediainfo/mediainfo-marketing/fssai-orders-dabur-to-halt-sale-of-food-products-carrying-misleading-100-claims-12225605"),
 ("B2B 구매, AI 에이전트에 위임되며 SEO 공식 붕괴",
  "B2B 구매자들이 제품 리서치를 AI 에이전트에 맡기기 시작하면서 기존 SEO 전략이 힘을 잃고 있다고 마테크가 분석했다. 브랜드는 '사실 밀도' 높은 구조화된 콘텐츠로 AI 인용을 노려야 한다.",
  "MarTech", "https://martech.org/how-to-make-your-content-visible-to-ai-buying-agents/"),
 ("메타, 광고 매출 고속 성장 — AI 지출 불안도 공존",
  "메타의 2분기 광고 매출이 전년 대비 27% 성장한 594억 달러를 기록했다. 저커버그는 \"AI 투자가 성과를 내고 있다\"고 자신했지만 막대한 지출에 대한 투자자 불안은 여전하다.",
  "Marketing Dive", "https://www.marketingdive.com/news/meta-touts-industry-leading-ad-revenue-growth-but-ai-unease-rises/826583/"),
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
