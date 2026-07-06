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
 ("Anthropic, 신약 개발 위한 'Claude Science' 공식 출시",
  "Anthropic이 유전체·단백질체·화학정보학 등 60여 개 사전 구성 툴을 갖춘 연구 워크벤치 'Claude Science'를 공개했다. 방치질환 치료제 개발을 위한 내부 신약 개발 프로그램도 함께 시작한다고 밝혔다.",
  "pharmaphorum", "https://pharmaphorum.com/news/anthropic-launches-claude-science-pharma-researchers"),
 ("GPT-5.6 신모델군, 미국 정부 요청으로 '신뢰 파트너'만 접근 허용",
  "오픈AI가 공개한 GPT-5.6 Sol·Terra·Luna 모델이 미국 정부 요청에 따라 약 20개 검증된 파트너 기관으로만 접근이 제한됐다. 세 모델은 각각 성능·비용·속도에서 다른 포지셔닝을 취하고 있다.",
  "CNBC", "https://www.cnbc.com/2026/06/26/openai-limits-new-ai-models-to-trusted-partners-request-us-government.html"),
 ("xAI 그록5, 콜로서스2서 훈련 지속 — 3분기 출시 사실상 무산",
  "6조 파라미터급 그록5가 여전히 xAI의 기가와트급 슈퍼클러스터 콜로서스2에서 훈련 중이다. 예측시장에서는 3분기 출시 확률을 3%로 반영하는 등 지연 우려가 커지고 있다.",
  "AdwaitX", "https://www.adwaitx.com/grok-5-release-date-colossus-2-training/"),
 ("오픈AI, IPO 2027년 연기 검토 — 앤트로픽은 10월 상장 정조준",
  "오픈AI가 스페이스X 상장 이후 시장 변동성을 이유로 IPO를 2027년으로 미루는 방안을 검토 중이라고 전해졌다. 앤트로픽은 9650억 달러 밸류에이션을 발판 삼아 10월 나스닥 상장을 목표로 하고 있다.",
  "TechCrunch", "https://techcrunch.com/2026/06/08/following-anthropic-openai-files-confidentially-for-ipo/"),
 ("저커버그 \"AI 에이전트 발전 속도, 기대에 못 미쳤다\"",
  "마크 저커버그가 메타 내부에서 AI 에이전트의 진전이 예상보다 더디다고 언급한 것으로 전해졌다. 업계 전반에서 에이전트 상용화 속도에 대한 눈높이 조정이 이어지고 있다.",
  "TechCrunch", "https://techcrunch.com/2026/07/02/mark-zuckerberg-tells-staff-that-ai-agents-havent-progressed-as-quickly-as-hed-hoped/"),
 ("클라우드플레어, AI 크롤러 기본 차단 — '크롤당 과금'서 '활용당 과금'으로",
  "클라우드플레어가 광고가 붙은 페이지에서 검색·AI 학습을 동시에 수집하는 '혼합형' 크롤러를 9월 15일부터 기본 차단한다고 밝혔다. 콘텐츠가 AI 답변에 실제로 활용될 때 퍼블리셔에 보상하는 '페이 퍼 유즈' 모델도 함께 도입한다.",
  "TechCrunch", "https://techcrunch.com/2026/07/01/cloudflares-new-policy-pushes-ai-companies-to-pay-for-publishers-content/"),
 ("백악관, AI 혁신·안보 촉진 행정명령 서명",
  "미국 백악관이 첨단 AI의 혁신과 안보를 동시에 촉진하기 위한 행정명령에 서명했다. 정부 차원의 AI 정책 개입이 모델 공개 범위에까지 영향을 미치고 있다.",
  "The White House", "https://www.whitehouse.gov/presidential-actions/2026/06/promoting-advanced-artificial-intelligence-innovation-and-security/"),
]

DESIGN = [
 ("피그마 Config 2026 — 모션·코드·셰이더로 캔버스 전면 재정의",
  "피그마가 연례 컨퍼런스 Config 2026에서 모션, 코드 레이어, 셰이더를 캔버스의 새로운 '재료'로 도입했다고 발표했다. 단순 기능 추가가 아닌 디자인 툴 자체의 플랫폼 전환으로 평가된다.",
  "Figma Blog", "https://www.figma.com/blog/config-2026-recap/"),
 ("피그마 모션, 타임라인 기반 네이티브 애니메이션 도구 공개",
  "피그마가 키프레임과 프리셋을 지원하는 타임라인을 캔버스에 직접 얹은 '피그마 모션'을 선보였다. 디자인 시스템에 애니메이션을 한 번만 적용하면 모든 화면과 협업자 파일에 그대로 전파된다.",
  "uwarp.design", "https://www.uwarp.design/blog/figma-config-2026-recap-code-motion-agent"),
 ("피그마, 코드 없이 나만의 툴 만드는 '생성형 플러그인' 공개",
  "피그마가 원하는 동작과 컨트롤을 말로 설명하면 전용 플러그인이 만들어지는 '생성형 플러그인'을 공개했다. 개발 환경이나 플러그인 API 지식 없이도 팀 맞춤 도구를 만들 수 있다.",
  "Classmethod", "https://dev.classmethod.jp/en/articles/figma-config-2026-recap/"),
 ("뉴 디자이너스 2026, 런던서 나흘간 신진 창작자 쇼케이스",
  "런던 이즐링턴 비즈니스 디자인 센터에서 열린 '뉴 디자이너스 2026'이 기존 2주 일정을 나흘로 압축해 진행됐다. 다양한 분야의 신진 창작 인재를 소개하는 행사로 자리매김했다.",
  "It's Nice That", "https://www.itsnicethat.com/articles/new-designers-2026-creative-industry-sponsored-content-040626"),
 ("크리에이티브 업계 설문 \"번아웃 69%, AI 긍정 평가는 10%뿐\"",
  "크리에이티브 붐의 2026년 업계 설문에서 응답자의 69%가 최근 1년간 번아웃을 겪었다고 답했다. 86%가 AI 툴을 업무에 쓰고 있지만 전반적 영향이 긍정적이라는 응답은 10%에 그쳤다.",
  "Creative Boom", "https://www.creativeboom.com/news/the-state-of-the-creative-industry-2026-what-our-survey-tells-us-about-money-burnout-and-ai/"),
 ("블루마린, 몰타 대표 맥주 '치스크'에 세션비어 아이덴티티 신설",
  "브랜드 디자인 에이전시 블루마린이 몰타의 국민 맥주 치스크의 저알코올 신제품 '치스크 세션'의 브랜드·패키지 디자인을 맡았다. 기존 치스크의 노란색과 몰타 십자 문양은 유지하되 채도 높은 블루로 저알코올을 시각화했다.",
  "The Dieline", "https://thedieline.com/cisk-beer-relaunches-with-modern-brand-livery-celebrating-its-maltese-heritage/"),
 ("브루넬의 SS 그레이트브리튼, '브리스톨 도크야드'로 리브랜드",
  "브리스톨의 유서 깊은 관광 명소 'SS 그레이트브리튼'이 운영 재단 명칭을 '브리스톨 도크야드'로 바꾼다. 배 자체의 이름은 유지되며, 인근 도크까지 아우르는 복합 문화 캠퍼스로 확장하려는 재단의 장기 목표를 반영했다.",
  "Transform magazine", "https://www.transformmagazine.net/articles/2026/ss-great-britain-undergoes-name-change-in-rebrand-bristol-dockyards/"),
]

MARKETING = [
 ("버셀, 메르세데스 AMG 페트로나스 F1팀과 다년 파트너십 체결",
  "클라우드 플랫폼 버셀이 메르세데스 AMG 페트로나스 F1팀과 다년 전략적 파트너십을 발표했다. 브리티시 그랑프리부터 레이스카에 브랜드가 노출되며, 팀 디지털 인프라 고도화까지 협력 범위를 넓힌다.",
  "Mercedes-AMG F1", "https://www.mercedesamgf1.com/news/mercedes-amg-petronas-f1-team-and-vercel-announce-multi-year-strategic-partnership"),
 ("Palantir-Zeta Global, AI 마케팅 인프라 위해 손잡아",
  "제타 글로벌이 자사 데이터 클라우드를 팔란티어 파운드리 위에 재구축하는 전략적 제휴를 발표했다. AI 엔진 '아테나'가 더 폭넓은 기업 데이터를 실시간으로 활용해 마케팅 의사결정을 자동화하게 된다.",
  "Pulse2", "https://pulse2.com/palantir-and-zeta-global-partner-to-build-ai-marketing-infrastructure/"),
 ("디스코, LLM 광고 효과 처음 측정하는 'AI 서치 리프트' 출시",
  "디스코가 광고가 챗봇·AI 검색 안에서 브랜드 언급을 실제로 늘리는지 측정하는 'AI 서치 리프트'를 출시했다. 소비자 동의 기반 패널로 노출군과 대조군을 비교하는 방식이다.",
  "MarTech360", "https://martech360.com/news/stack-platforms/disqo-launches-ai-search-lift-to-measure-the-incremental-impact-of-advertising-on-conversational-ai-and-llm-platforms/"),
 ("액티브캠페인, 브랜드 보이스 기억하는 'Active Intelligence 2.8' 출시",
  "액티브캠페인이 로고·컬러·톤 등 브랜드 가이드라인을 기억해 캠페인 초안을 만드는 업데이트를 내놨다. 회사는 캠페인 제작 속도가 8배 빨라진다고 설명했다.",
  "MarTech", "https://martech.org/the-latest-ai-powered-martech-news-and-releases/"),
 ("메타, AI 비즈니스 어시스턴트 전 세계 광고주로 확대",
  "메타가 광고 관리자·비즈니스 스위트에 내장된 AI 비즈니스 어시스턴트를 미국·유럽·아태·중남미 전 광고주로 확대했다. 캠페인 성과 분석과 계정 문제 해결을 자연어로 지원한다.",
  "Social Media Today", "https://www.socialmediatoday.com/news/meta-expands-access-to-ai-business-assistant/818263/"),
 ("메타, 스레드·릴스에 캐러셀·트렌딩 등 신규 광고 포맷 확장",
  "메타가 스레드에 캐러셀·4:5 이미지 광고를, 릴스에는 트렌딩 광고 포맷을 넓혀 광고주에 개방했다. 릴스 트렌딩 광고는 비보조 인지도가 20% 오르는 초기 테스트 결과를 냈다.",
  "Social Media Today", "https://www.socialmediatoday.com/news/meta-adds-more-reels-and-threads-ad-options/759946/"),
 ("DASH, 역대 최대 미디어 투자로 캠페인 확장",
  "음료 브랜드 DASH가 'The Drink That Has It All' 캠페인에 브랜드 역사상 최대 규모의 미디어 투자를 단행했다. 7월 한 달간 영국 성인 7명 중 1명에게 평균 25회 노출되는 것을 목표로 한다.",
  "Food and Drink Technology", "https://www.foodanddrinktechnology.com/news/68352/food-drink-campaigns-round-up-july-2026/"),
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
