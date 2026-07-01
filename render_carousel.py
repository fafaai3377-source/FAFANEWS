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
 ("앤트로픽, 과학자용 AI 워크벤치 '클로드 사이언스' 출시",
  "앤트로픽이 과학 연구를 위한 AI 워크벤치 '클로드 사이언스'를 출시했다. 새로운 모델이 아니라 기존 클로드 모델을 기반으로 60개 이상의 과학 데이터베이스와 도구를 통합했으며, 단백질 구조 예측 등 생물학·화학 작업을 자동화한다. 주 AI 어시스턴트가 프로젝트를 관리하고 별도의 팩트체커 AI가 인용과 계산을 검증하는 멀티 에이전트 구조를 채택했다.",
  "TechCrunch", "https://techcrunch.com/2026/06/30/anthropics-claude-science-bets-on-workflow-not-a-new-model-to-win-over-scientists/"),
 ("中 메이투안, 자국산 칩으로만 훈련한 1.6조 파라미터 모델 공개",
  "중국 메이투안이 자국산 칩만으로 사전학습과 추론을 모두 수행한 1.6조 파라미터 오픈소스 모델 '롱캣-2.0'을 공개했다. 100만 토큰 컨텍스트 윈도우를 지원하며 5만 개의 국산 칩으로 처음부터 훈련됐다. 딥시크가 추론 단계에서만 국산 칩을 썼던 것과 달리, 계산 집약적인 사전학습 단계에서도 자국 기술력을 입증했다는 평가다.",
  "South China Morning Post", "https://www.scmp.com/tech/tech-trends/article/3358854/china-debuts-biggest-ai-model-trained-local-chips-meituan-releases-longcat-20"),
 ("메타, AI 기반 예측시장 앱 '아레나' 자체 개발 중",
  "메타가 칼시 인수를 검토했다가 무산된 이후, 자체 예측시장 앱 '아레나'를 개발하고 있는 것으로 확인됐다. 실제 현금 대신 '가상 머니'로 베팅하는 방식이며, 메타의 AI 시스템이 질문 생성과 승패 판정을 담당한다. 칼시·폴리마켓의 월간 거래량이 1년 새 280억 달러에서 2200억 달러로 급증한 시장 상황이 배경이다.",
  "NPR", "https://www.npr.org/2026/06/30/nx-s1-5875468/meta-kalshi-prediction-market-acquisition-talks"),
 ("EU 이사회, AI법 간소화 패키지 최종 승인",
  "유럽연합 이사회가 6월 29일 AI법 간소화 패키지에 최종 승인을 내렸다. 이에 따라 고위험 AI 시스템의 적용 시한이 단독 시스템은 2027년 12월, 규제 제품 내장형은 2028년 8월로 각각 연기됐다. 아울러 실제 인물의 나체·성적 이미지를 생성하는 비동의 딥페이크 콘텐츠에 대한 새로운 금지 조항도 추가됐다.",
  "Council of the EU", "https://www.consilium.europa.eu/en/press/press-releases/2026/06/29/artificial-intelligence-council-gives-final-green-light-to-simplify-and-streamline-rules/"),
 ("AWS, AI 도입 가속화 위해 10억 달러 투입해 '현장 엔지니어' 조직 신설",
  "아마존웹서비스가 기업 고객사에 AI 엔지니어를 직접 파견하는 '포워드 디플로이드 엔지니어링' 조직을 신설하며 10억 달러를 투자한다고 밝혔다. 약 45일 주기로 5~6명의 엔지니어가 고객사에 상주해 에이전틱 AI 시스템 구축을 지원하며, NFL·NBA 등이 이미 이 조직과 협업 중이다.",
  "CNBC", "https://www.cnbc.com/2026/06/30/aws-amazon-ai-forward-deployed-engineers.html"),
 ("SK하이닉스, AI 메모리 호황 업고 나스닥 상장 신청",
  "SK하이닉스가 미국 증권거래위원회에 나스닥 상장을 위한 예탁증권 신청서를 제출했다. 티커명은 'SKHY'이며 약 294억 달러 규모 조달을 목표로 한다. HBM 시장 점유율 약 60%를 차지하는 AI 메모리 반도체 선두주자로, 조달 자금은 신규 생산시설과 EUV 장비 확보에 쓰일 예정이다.",
  "Investing.com (Reuters)", "https://www.investing.com/news/stock-market-news/sk-hynix-applies-to-file-for-nasdaq-ipo-432SI-4767438"),
 ("엔비디아·퍼머스, 인도네시아에 17만 GPU 규모 AI 데이터센터 구축",
  "AI 인프라 기업 퍼머스가 엔비디아와 손잡고 인도네시아 바탐에 최대 17만 개의 GPU를 배치하는 360메가와트 규모 AI 데이터센터를 구축한다. 2034년까지 이어지는 8년 계약으로, 향후 6년간 250억~300억 달러 규모의 고객 선약 매출을 확보할 것으로 예상된다.",
  "DataCenterDynamics", "https://www.datacenterdynamics.com/en/news/firmus-to-deploy-170000-gpu-cluster-in-batam-indonesia/"),
]

DESIGN = [
 ("스택오버플로우, AI 시대 '커뮤니티'로 정체성 재정의",
  "디자인 스튜디오 Koto가 스택오버플로우의 브랜드를 새로 디자인했다. 새 아이덴티티는 'Always in Build'라는 컨셉과 커스텀 서체 Stack Sans를 중심으로 하며, AI가 확산될수록 신뢰할 수 있는 인간 커뮤니티의 지식이 더 중요해진다는 전략에서 출발했다.",
  "Creative Boom", "https://www.creativeboom.com/news/koto-reframes-stack-overflow-around-the-one-thing-ai-cant-replace-its-community/"),
 ("공룡학회 로고, SAND 손끝에서 새로 태어나다",
  "디자인 스튜디오 SAND가 응용고생물학회의 새 로고를 공개했다. 그런지 텍스처와 산세리프 서체를 활용해 공룡·화석이라는 학문적 정체성을 시각적으로 담아냈다. 해당 프로젝트는 브랜드뉴의 옹호단체 부문에 소개됐다.",
  "Brand New (UnderConsideration)", "https://www.underconsideration.com/brandnew/archives/new_logo_for_association_of_applied_paleontological_sciences_by_sand.php"),
 ("글루텐프리 빵 브랜드 유디스, 패키지 새 단장",
  "헤이스택 스튜디오가 글루텐프리 식품 브랜드 유디스의 로고와 패키지를 새로 디자인했다. 둥근 세리프 서체와 일러스트레이션을 활용해 친근한 인상을 강화했으며, 말장난 태그라인이 함께 소개됐다.",
  "Brand New (UnderConsideration)", "https://www.underconsideration.com/brandnew/archives/new_logo_and_packaging_for_udis_by_haystack_studios.php"),
 ("V&A 이스트 뮤지엄, '만들기'를 그래픽으로 구현하다",
  "디자인 스튜디오 APFEL이 런던 스트랫퍼드에 새로 문을 연 V&A 이스트 뮤지엄의 전시 그래픽과 사이니지를 디자인했다. LED 조명 스트립으로 글자를 구성하는 스텐실 형태의 커스텀 서체를 개발해 동런던의 산업 유산을 반영했다.",
  "Creative Boom", "https://www.creativeboom.com/inspiration/the-act-of-making-is-at-the-heart-of-apfels-exhibition-graphics-for-va-east-museum/"),
 ("창작업계 종사자 69%, 번아웃 겪었다",
  "크리에이티브붐이 창작 전문가 882명을 대상으로 진행한 2026년 설문조사 결과를 발표했다. 응답자의 69%가 지난 1년간 번아웃을 겪었고, 86%가 AI 도구를 사용하지만 AI의 영향이 긍정적이라고 답한 비율은 10%에 불과했다.",
  "Creative Boom", "https://www.creativeboom.com/news/the-state-of-the-creative-industry-2026-what-our-survey-tells-us-about-money-burnout-and-ai/"),
 ("칸 라이언즈 2026, 'AI 홍수 속 아날로그의 반격'",
  "칸 라이언즈 2026 현장을 취재한 이 기사는 창작업계가 대규모 도달률 중심에서 의미 있는 커뮤니티 참여 중심으로 이동하고 있다고 분석한다. AI 콘텐츠가 넘쳐나는 환경에서 공명, 니치 타깃, 아날로그적 경험이 새로운 경쟁력으로 떠오르고 있다고 진단했다.",
  "Creative Boom", "https://www.creativeboom.com/insight/cannes-lions-2026-why-resonance-niche-and-analogue-are-the-future-of-creativity/"),
 ("잉글랜드 응원 굿즈, 기도하는 몸짓을 오브제로",
  "스튜디오 해피엔딩이 잉글랜드 축구 팬들이 승부차기 때 취하는 기도 자세에서 착안해 '잉글랜드 기도 묵주' 60세트를 한정 제작했다. 적색과 원목 비즈, 금색 축구공 펜던트로 구성됐으며, SNS 보물찾기 방식으로 배포해 물리적 오브제로 브랜드 메시지를 전달했다.",
  "Creative Boom", "https://www.creativeboom.com/news/this-clever-world-cup-campaign-shows-how-physical-objects-can-create-meaning-beyond-traditional-advertising/"),
]

MARKETING = [
 ("다부르, 강굴리 AI 코칭으로 母心 공략",
  "인도 다부르의 글루코플러스C가 크리켓 레전드 소우라브 강굴리를 활용한 생성형 AI 기반 개인화 코칭 캠페인을 출시했다. 어머니가 자녀의 이름과 종목을 입력하면 강굴리가 직접 격려하는 맞춤형 영상을 왓츠앱으로 받는 방식이다. 250개 이상의 콘텐츠 변형으로 130만 개 이상의 개인화 영상을 제작해 인도 전역 1억 1천만 명 이상의 어머니에게 도달했다.",
  "BestMediaInfo", "https://bestmediainfo.com/mediainfo/mediainfo-marketing/daburs-glucoplus-c-uses-ai-to-deliver-personalised-coaching-from-sourav-ganguly-in-latest-campaign-12120128"),
 ("세계옥외광고기구, OOH 측정 가이드라인 2.0 발표",
  "세계옥외광고기구가 런던에서 2022년 이후 최대 규모의 옥외광고 관중측정 가이드라인 2.0을 공개했다. 이번 개정판은 28개 지역 측정기관이 참여했으며, 인도의 모바일 GPS 기반 측정 모델 로드스타가 대표 사례로 소개됐다.",
  "BestMediaInfo", "https://bestmediainfo.com/mediainfo/advertising/woo-launches-new-global-ooh-measurement-guidelines-highlights-indias-roadstar-model-12119145"),
 ("프라이드 마케팅, 6월 지나면 침묵하는 브랜드들",
  "6월 프라이드 먼스에는 다수 브랜드가 LGBTQIA+ 지지 캠페인을 활발히 펼치지만 7월 이후에는 관련 메시지가 사라지는 현상이 지적됐다. 전문가들은 이를 계절성 마케팅에 불과하다고 비판하며, 진정한 포용성은 채용과 직장 정책에서의 일관된 대표성에서 나온다고 지적했다.",
  "BestMediaInfo", "https://bestmediainfo.com/mediainfo/mediainfo-marketing/every-june-brands-rediscover-pride-but-what-happens-in-july-12119082"),
 ("인도 수제맥주 카티 파탕, 매출 12억루피 돌파 후 스케일업",
  "인도 수제맥주 브랜드 카티 파탕이 2026회계연도 연결매출 12억 2천만 루피를 기록하며 니치 전략에서 규모 확장 전략으로 전환한다고 밝혔다. 다음 달 마디아프라데시 출시를 앞두고 있으며, 영국 옥스퍼드 양조장 지분 51%를 인수하며 해외 진출도 본격화했다.",
  "BestMediaInfo", "https://bestmediainfo.com/mediainfo/mediainfo-marketing/after-crossing-rs-12-crore-in-revenue-kati-patang-shifts-focus-from-niche-craft-to-scale-12118923"),
 ("레딧, AI 학습 데이터의 핵심 공급원으로 부상",
  "레딧 최고운영책임자 젠 웡이 팟캐스트 인터뷰에서 레딧이 구글, 오픈AI와 맺은 데이터 라이선싱 계약을 통해 AI 모델 학습의 핵심 데이터원으로 자리잡았다고 밝혔다. 웡은 AI 생성 콘텐츠로부터 플랫폼의 인간 중심성을 지키는 것이 중요한 과제라고 강조했다.",
  "AdExchanger", "https://www.adexchanger.com/adexchanger-talks/reddit-is-training-the-robots/"),
 ("세일즈포스, AI 쇼핑 에이전트 3종 정식 출시",
  "세일즈포스가 쇼퍼·바이어·머천트 에이전트로 구성된 에이전트포스 커머스를 정식 출시하고 챗GPT와의 네이티브 통합을 발표했다. 이 AI 에이전트는 재고 확인부터 배송 옵션 비교, 결제까지 단일 대화 안에서 처리하도록 설계됐다.",
  "Salesforce Newsroom", "https://www.salesforce.com/news/stories/agentforce-commerce-announcement/"),
 ("적은 예산으로 이기는 챌린저 브랜드의 마케팅 공식",
  "대기업과 경쟁하는 중소 챌린저 브랜드들이 제한된 예산으로도 성과를 내는 전략이 주목받고 있다. 견과류 브랜드 에메랄드너츠는 야구장에서 촬영한 유머러스한 광고로 밀레니얼 세대 참여율 11%, 신규 구매 30% 증가를 이끌어냈다.",
  "Marketing Dive", "https://www.marketingdive.com/news/how-challenger-brands-can-stand-out-with-fewer-marketing-swings/823549/"),
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
