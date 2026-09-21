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
 ("트럼프, AI 규제 대신 'AI 포스' 창설과 AI 차르 임명 발표",
  "트럼프 대통령이 AI 산업을 감독할 'AI 포스'를 창설하고 'AI 차르'를 임명하겠다고 발표했다. 그는 AI 규제 강화 요구를 혹스라고 부르며 산업 성장을 저해하지 않겠다고 밝혔다.",
  "NBC News", "https://www.nbcnews.com/politics/white-house/artificial-intelligence-task-force-czar-technology-trump-rcna598688"),
 ("'Plugin4Shell' 제로클릭 취약점, AI 코딩 에이전트 4종 강타",
  "'Plugin4Shell'이라는 제로클릭 원격코드실행 취약점이 클로드 코드, 오픈AI 코덱스, 깃허브 코파일럿, 제미나이 CLI 등 4대 AI 코딩 에이전트에서 발견됐다. 앤트로픽과 오픈AI는 패치를 배포했으나 마이크로소프트 코파일럿은 아직 미패치 상태다.",
  "Help Net Security", "https://www.helpnetsecurity.com/2026/09/18/plugin4shell-ai-coding-agents-vulnerability/"),
 ("AI 업계, 정말 속도 조절할 준비 됐나",
  "앤트로픽 CEO 다리오 아모데이가 AI 개발 속도 조절 방안을 제시했지만 엔비디아 CEO 젠슨 황은 공개적으로 반대했다. 독립적 제3자 평가 기관 도입 등 안전 제안이 실제 구속력 있는 조치로 이어질지는 미지수다.",
  "TechCrunch", "https://techcrunch.com/2026/09/20/is-the-ai-industry-really-ready-to-slow-down/"),
 ("월드 모델 스타트업들, 투자는 대규모인데 계획은 '비밀'",
  "얀 르쿤의 AMI 랩스, 페이페이 리의 월드 랩스 등 월드 모델 스타트업들이 대규모 투자를 유치하면서도 구체적 제품·상업화 계획은 비공개로 유지하고 있다. 데이터 공급사조차 방향성을 알지 못해 협업에 어려움을 겪는다.",
  "TechCrunch", "https://techcrunch.com/2026/09/20/world-model-companies-are-keeping-a-lot-of-secrets/"),
 ("앤트로픽, AI 연구개발 속도 측정하는 투명성 지표 첫 공개",
  "앤트로픽이 AI가 직접 수행하는 연구개발 비율, 에이전트 감독 현황, 안전 작업 투입 컴퓨팅 비중 등 3가지 지표를 처음 공개했다. 클로드는 현재 앤트로픽 모델 연구개발 업무의 26%를 주도하며 약 3만 개의 연구용 에이전트가 실시간 모니터링되고 있다.",
  "Anthropic 공식 블로그", "https://www.anthropic.com/institute/measuring-pace-of-ai-development"),
 ("MS AI 총괄 술레이만, '규제 미루지 말아야'",
  "마이크로소프트 AI 총괄 무스타파 술레이만이 중국과의 AI 경쟁을 이유로 자국 내 규제를 미뤄서는 안 된다고 밝혔다. 그는 규제를 안전을 위한 공동 규범 마련으로 봐야 한다고 강조했다.",
  "Seeking Alpha", "https://seekingalpha.com/news/4644588-microsoft-ai-chief-urges-guardrails-as-white-house-resists-broad-regulation"),
 ("반지형 웨어러블 보치, AI로 회의록 자동 정리",
  "스타트업 보치가 249달러짜리 반지형 웨어러블을 출시했다. 더블탭으로 녹음을 시작·종료하며 AI가 회의 내용을 자동 요약하지만, 타인 인지 없이 녹음될 수 있다는 사생활 우려도 제기됐다.",
  "TechCrunch", "https://techcrunch.com/2026/09/20/voccis-ring-adds-a-new-form-factor-to-meeting-note-taking/"),
]

DESIGN = [
 ("닌텐도, 슈퍼 마리오 로고 은근슬쩍 업데이트",
  "닌텐도가 슈퍼 마리오 40주년 행사 종료 후 공식 캐릭터 페이지의 로고를 베벨과 그라디언트가 적용된 3D 스타일로 바꿨다. 두 줄이던 문구는 한 줄 가로 배치로 바뀌었고 색상과 서체는 유지됐다.",
  "Nintendo Life", "https://www.nintendolife.com/news/2026/09/nintendo-appears-to-have-updated-the-super-mario-logo"),
 ("프리츠 한센 x 테크닉스, 바우하우스풍 '사운드 클럽' 공개",
  "덴마크 가구 브랜드 프리츠 한센이 오디오 브랜드 테크닉스와 협업해 런던 쇼룸을 바우하우스풍 리스닝 공간으로 꾸몄다. 런던 디자인 페스티벌 기간 스터디·리딩 코너 등 4개 공간에 빈티지 턴테이블과 가구를 전시했다.",
  "Dezeen", "https://www.dezeen.com/2026/09/20/fritz-hansen-sound-club-london/"),
 ("브루스 마우 디자인, 로열 위니펙 발레단 새 아이덴티티 공개",
  "브루스 마우 디자인이 캐나다 로열 위니펙 발레단의 새 로고와 아이덴티티를 제작했다. 파란색 콘덴스드 산세리프 서체와 무용의 역동성을 반영한 키네틱 모션 요소를 적용했다.",
  "Brand New", "https://www.underconsideration.com/brandnew/archives/new_logo_and_identity_for_the_royal_winnipeg_ballet_by_bruce_mau_design.php"),
 ("펜타그램 마리나 윌러, 빛과 프로젝션으로 만든 BFI 영화제 아이덴티티",
  "펜타그램 파트너 마리나 윌러가 검은 배경에 색조명을 투사해 'LFF' 글자를 만들고 촬영하는 방식으로 2026 BFI 런던 영화제 아이덴티티를 제작했다. 다른 영화제가 쓰지 않는 라이트블루 컬러로 포용적이고 현대적인 이미지를 표현했다.",
  "It's Nice That", "https://www.itsnicethat.com/articles/marina-willer-pentagram-bfi-london-film-festival-graphic-design-160926"),
 ("디프시티 허브 로고, 서체 대신 영국 수어로 만들다",
  "청각장애인 복합문화공간 '디프시티 허브'의 로고를 에이전시 템플로가 일반 서체 대신 영국 수어(BSL) 손동작을 모션 트래킹으로 캡처해 원형 체인 형태 글자로 완성했다.",
  "Creative Boom", "https://www.creativeboom.com/news/asking-what-typeface-deafcity-hub-used-for-its-logo-is-the-wrong-question/"),
 ("캔바, 어피니티·캐벌리 통합한 '프로스위트' 출시",
  "캔바가 어피니티, 캐벌리, 플러리시, 레오나르도를 통합한 프로스위트를 출시하며 100개 이상의 신기능을 추가했다. 어피니티·캐벌리 핵심 기능은 무료로 유지하면서 요청이 많던 블렌드 툴 등을 새로 도입했다.",
  "Creative Boom", "https://www.creativeboom.com/news/canva-launches-prosuite-bringing-affinity-cavalry-flourish-and-leonardo-together-with-more-than-new-100-features/"),
 ("피그마 위브, 커뮤니티 공유 툴 5선 소개",
  "피그마가 커뮤니티 퍼블리싱 기능을 AI 워크플로 툴 '위브'에도 적용했다. 인테리어 무드보드를 3D 렌더로 바꾸거나 손에 네일아트를 미리 적용해보는 툴 등 대표 사례 5가지를 소개했다.",
  "Figma Blog", "https://www.figma.com/blog/try-these-5-weave-tools-and-share-your-own/"),
]

MARKETING = [
 ("컴포저블 vs 패키지형 CDP, 무엇을 골라야 할까",
  "MarTech이 클라우드 데이터 웨어하우스 기반 컴포저블 CDP와 패키지형 CDP를 선택할 때 고려할 4가지 기준을 정리했다. 데이터 인프라 준비도, 엔지니어링 리소스, 실시간 지연 요구사항, 비용 구조가 핵심이다.",
  "MarTech", "https://martech.org/how-to-evaluate-composable-versus-packaged-cdp/"),
 ("소비자 81%, AI Chatbot 안내로 구매 포기한 경험",
  "Semrush 조사에 따르면 챗봇으로 쇼핑하는 소비자의 약 81%가 AI 안내를 근거로 구매를 포기한 적이 있다고 답했다. AI가 판매를 돕는 동시에 부정적 리뷰·경쟁사 정보를 노출시켜 구매를 막는 이중적 역할을 한다.",
  "MarTech", "https://martech.org/ai-is-telling-consumers-not-to-buy-your-product/"),
 ("메타, 크리에이터·브랜드 연결하는 마케팅 허브 확대",
  "메타가 크리에이터 마케팅 허브를 전 세계에 출시해 확장된 API, AI 기반 크리에이터 검색, 파트너십 메시징 기능을 제공한다. 페이스북 크리에이터도 인스타그램과 같은 마켓플레이스에 통합됐다.",
  "Marketing Dive", "https://www.marketingdive.com/news/meta-streamlines-creator-brand-tie-ups-with-new-marketing-hub/830593/"),
 ("애플비스, 인기 버거 힘입어 치즈 메뉴 플랫폼 확장",
  "애플비스가 지난 1월 선보인 'O-M-Cheese 버거'가 출시 몇 달 만에 최다 판매 버거가 되며 소셜미디어에서 90억 회 노출을 기록했다. 이 인기에 힘입어 치즈 테마 메뉴 전체를 확장하고 있다.",
  "Marketing Dive", "https://www.marketingdive.com/news/applebees-omcheese-platform-expansion/830642/"),
 ("스테이플스, '하드 버튼'으로 B2B 서비스 알리기 나서",
  "스테이플스가 상징적인 빨간 '이지 버튼'과 대비되는 검은색 '하드 버튼' 캠페인을 시작했다. 포춘 500대 기업 75%와 거래하면서도 저평가됐던 가구 컨설팅·공급망 등 B2B 서비스를 알리려는 전략이다.",
  "Marketing Dive", "https://www.marketingdive.com/news/staples-cmo-on-introducing-a-hard-button-amid-bigger-b2b-push/830611/"),
 ("저가항공사, '최저가순' 경쟁 밖으로 움직이다",
  "진에어·에어부산·에어서울 합병, 티웨이항공의 트리니티항공 전환 등 국내 저가항공사들이 가격 경쟁을 넘어 각기 다른 차별화 전략을 펴고 있다. 노선 폭·맞춤 서비스·운영 안정성이 새 선택 기준으로 떠올랐다.",
  "모비인사이드", "https://www.mobiinside.co.kr/2026/09/18/korean-lcc-market-repositioning/"),
 ("AI가 광고 타깃 정하는 시대, 세분화 전략은 손해",
  "메타 AI가 광고 노출 대상을 직접 결정하면서 과거의 세분화된 타깃팅 전략은 효율이 떨어지고 구매 이유별 소재 제작이 중요해졌다. 메타는 이 AI 개선으로 광고 전환율 6%, 노출 19%, 매출 33% 상승 효과를 봤다고 밝혔다.",
  "모비인사이드", "https://www.mobiinside.co.kr/2026/09/18/ai-ads-creative-appeal-strategy/"),
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
