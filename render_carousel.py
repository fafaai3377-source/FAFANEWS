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
 ("오픈AI 출신 연구원, AI 신약 스타트업 창업 추진",
  "오픈AI 연구원 마일스 왕이 AI 신약 재창출 스타트업 창업을 추진 중이다. 라이트스피드벤처파트너스 주도로 약 2억 달러를 조달해 20억 달러 기업가치를 인정받는 방안이 논의되고 있다. 기존 약물이나 임상시험에 실패한 약물의 새로운 용도를 찾는 데 주력할 전망이다.",
  "TechCrunch", "https://techcrunch.com/2026/07/14/openai-researcher-miles-wang-in-talks-to-launch-ai-drug-discovery-startup-valued-at-2b/"),
 ("TYLsemi, AI 칩렛 스타트업 430억원 시드 유치",
  "반도체 스타트업 TYLsemi가 매터벤처파트너스 주도로 약 4300만 달러 규모의 초기 투자를 유치했다. 커넥티비티·전력·메모리 칩렛과 설계 플랫폼을 결합해 커스텀 AI 가속기 개발 비용과 기간을 절반으로 줄이는 것이 목표다.",
  "SiliconANGLE", "https://siliconangle.com/2026/07/14/custom-ai-chip-design-startup-tylsemi-launches-43m-early-stage-funding/"),
 ("씽킹머신즈 '잉클링' 공개 — 9750억 파라미터 오픈모델",
  "전 오픈AI CTO 미라 무라티가 이끄는 씽킹머신즈랩이 첫 오픈웨이트 모델 '잉클링'을 공개했다. 총 9750억, 활성 410억 파라미터의 멀티모달 MoE 모델로 45조 토큰으로 학습됐으며 아파치 2.0 라이선스로 공개돼 현재까지 가장 큰 미국산 오픈웨이트 모델이 됐다.",
  "Thinking Machines Lab", "https://thinkingmachines.ai/news/introducing-inkling/"),
 ("중국 AI 동반자 규제 시행 — 두바오·큐원 기능 중단",
  "중국의 'AI 의인화 상호작용 서비스 관리 임시조치'가 7월 15일부터 시행되면서 바이트댄스 두바오와 알리바바 큐원이 개인화된 AI 동반자·에이전트 기능을 중단했다. 중독 방지 시스템과 즉시 종료 기능 등을 의무화한 규제다.",
  "Tech Times", "https://www.techtimes.com/articles/320525/20260715/china-ai-companion-law-takes-effect-doubao-qwen-shut-down-millions-lose-chat-data.htm"),
 ("xAI, 무허가 가스터빈 59기 논란 — NAACP 소송",
  "일론 머스크의 xAI가 콜로서스2 데이터센터 가동을 위해 미시시피주 사우스헤이븐에서 연방 대기오염 허가 없이 가스터빈 59기를 가동한 것으로 드러났다. 전미유색인지위향상협회(NAACP)는 가동 중단과 처벌을 요구하는 소송을 제기했다.",
  "Technology.org", "https://www.technology.org/2026/07/15/xai-59-unpermitted-gas-turbines-southaven-colossus-2/"),
 ("앤스로픽(Anthropic)·블랙스톤, 기업 AI 서비스 '오드' 출범",
  "앤스로픽이 블랙스톤, 헬먼앤프리드먼과 함께 기업에 AI 엔지니어를 파견하는 합작사 '오드 위드 앤스로픽'을 출범했다. 골드만삭스, 제너럴 아틀랜틱, 세쿼이아 캐피털 등이 투자자로 참여했으며 약 100명의 엔지니어가 기업 맞춤형 AI 도입을 지원한다.",
  "HPCwire (AIwire)", "https://www.hpcwire.com/aiwire/2026/07/15/anthropic-blackstone-and-hellman-friedman-introduce-ode-with-anthropic-an-enterprise-ai-services-firm/"),
 ("엔비디아, 아시아 칩 구매사 화이트리스트 절반 감축",
  "엔비디아가 첨단 AI 칩 구매를 승인했던 아시아 고객사 중 절반 이상을 새로운 내부 화이트리스트에서 제외했다. 싱가포르, 말레이시아, 일본을 중심으로 심사를 강화해 우회 경로를 통한 중국행 칩 유출을 차단하려는 조치다.",
  "DigiTimes", "https://www.digitimes.com/news/a20260714VL218/nvidia-chips-taiwan.html"),
]

DESIGN = [
 ("틴더, 10년 만에 브랜드 아이덴티티 전면 개편",
  "포르투 로샤(Porto Rocha)가 틴더의 약 10년 만의 첫 리브랜딩을 진행했다. 불꽃 아이콘을 새롭게 다듬고 워드마크를 세리프체 대문자로 전환했으며, 기존 red·orange 중심 팔레트에 블루와 그린을 더해 색상 스펙트럼을 넓혔다.",
  "It's Nice That", "https://www.itsnicethat.com/features/porto-rocha-tinder-rebrand-graphic-design-spotlight-140726"),
 ("오젠, 마더디자인(Mother Design)과 오럴케어 브랜드 아이덴티티 공개",
  "영국 신생 오럴케어 브랜드 오젠이 마더 디자인(Mother Design)과 함께 새 아이덴티티를 공개하고 부츠(Boots) 매장에 입점했다. 카브드 카운터폼을 적용한 워드마크와 두 톤의 그린 컬러로 신뢰감과 전문성을 표현했다.",
  "Creative Review", "https://www.creativereview.co.uk/oral-care-brand-ozen-identity-mother-design-broody/"),
 ("스튜디오 킬른, BAFTA 4개 시상식 통합 아이덴티티",
  "스튜디오 킬른이 BAFTA의 영화·TV·게임·TV크래프트 등 4개 시상식을 하나로 묶는 '파티클' 기반 비주얼 시스템을 공개했다. 색색의 입자가 흘러 모여 BAFTA 특유의 다면체를 이루며 시상식별로 색상과 형태를 달리 적용했다.",
  "Creative Boom", "https://www.creativeboom.com/news/how-studio-kiln-built-one-particle-system-to-unify-all-four-of-baftas-awards/"),
 ("스택오버플로우, 코토와 새 로고·아이덴티티 공개",
  "개발자 커뮤니티 플랫폼 스택오버플로우가 디자인 스튜디오 코토(Koto)와 함께 새 로고와 아이덴티티를 발표했다. 기하학적 산세리프 서체를 도입하고 오렌지·블랙 중심 색상 체계로 전환해 기술 중심 브랜드 이미지를 강화했다.",
  "Brand New", "https://www.underconsideration.com/brandnew/archives/new_logo_and_identity_for_stack_overflow_by_koto.php"),
 ("코토, 합리적 가격의 서체 파운드리 'CcType' 출시",
  "글로벌 크리에이티브 에이전시 코토가 새 서체 파운드리 CcType을 선보였다. 공동창업자 조위 로든은 갱신료 없이 저렴한 가격 정책으로 양질의 타이포그래피 접근성을 높이겠다고 밝히며 '빌드 인 퍼블릭' 방식을 택했다.",
  "Creative Boom", "https://www.creativeboom.com/news/the-world-is-deserving-of-better-design-kotos-jowey-roden-on-his-mission-to-make-quality-typefaces-affordable/"),
 ("토론토 심포니, 언더라인 스튜디오와 새 아이덴티티",
  "토론토 심포니 오케스트라가 언더라인 스튜디오(Underline Studio)와 협업해 새 로고와 브랜드 시스템을 공개했다. 디돈(Didone) 계열 세리프 서체를 중심에 두고 다양한 매체에 적용 가능한 아이덴티티 체계를 구축했다.",
  "Brand New", "https://www.underconsideration.com/brandnew/archives/new_logo_and_identity_for_toronto_symphony_orchestra_by_underline_studio.php"),
 ("호비스·킹스밀 합병, 브랜드옵스가 새 아이덴티티 설계",
  "영국 제빵기업 얼라이드 베이커리스가 호비스 그룹 인수를 마무리하며 호비스·킹스밀·앨린슨스 등을 아우르는 '호비스 베이커리스'로 통합됐다. 브랜드옵스(BrandOpus)는 '엘리멘탈 파워' 콘셉트로 아이덴티티를 설계하고 'Nourishing the Nation' 슬로건을 내걸었다.",
  "Creative Boom", "https://www.creativeboom.com/news/hovis-and-kingsmill-are-now-one-business-and-brandopus-has-given-it-an-identity-with-no-wheat-in-sight/"),
]

MARKETING = [
 ("스냅챗, 딕스 스포팅굿즈와 리테일미디어 제휴",
  "스냅챗이 딕스 스포팅굿즈 미디어 부문, 라이브램프와 손잡고 광고주가 스냅챗 캠페인이 온라인·오프라인 매출에 미치는 영향을 직접 확인할 수 있는 데이터 클린룸 파트너십을 발표했다. 아디다스 파일럿 캠페인에서는 광고비 대비 12달러 이상의 매출 효과가 확인됐다.",
  "MediaPost", "https://www.mediapost.com/publications/article/416561/snapchat-dicks-partnership-aims-to-bridge-retail.html"),
 ("피자헛, '백 투 더 헛' 레트로 리워드 캠페인 공개",
  "피자헛이 로열티 회원이 브랜드 역사 지식을 겨루는 '백 투 더 헛' 캠페인을 시작했다. 디너 서비스 NY와의 스트리트웨어 협업과 저가형 '스로우백 밸류 메뉴'를 함께 선보이며 향수를 자극하는 전략을 택했다.",
  "Marketing Dive", "https://www.marketingdive.com/news/pizza-hut-serves-up-nostalgia-with-latest-rewards-program-push/825124/"),
 ("틱톡샵, 영국 셀러 대상 분석 기능 강화",
  "틱톡샵이 영국 판매자를 위한 '상품 트래픽 분석' 기능을 대폭 업그레이드했다. 콘텐츠 형식별 상품 순위, 라이브·영상·제휴 링크별 트래픽 소스, 전환 효율 비교 등 세부 데이터를 제공한다.",
  "Social Media Today", "https://www.socialmediatoday.com/news/tiktok-sellers-in-the-uk-get-improved-insights/825256/"),
 ("MLS, 역대 최대 규모 통합 마케팅 캠페인 시작",
  "MLS가 '땡스 월드, 위일 테이크 잇 프롬 히어' 캠페인을 공개했다. 메시, 손흥민, 베컴, 매튜 매커너히 등이 출연하며, 월드컵 종료 시점에 맞춰 리그 복귀를 알리는 구단 역사상 최대 규모의 조율된 마케팅이다.",
  "The Drum", "https://www.thedrum.com/news/mls-marketing-chief-radhika-duggal-says-fandom-is-built-after-the-final-whistle"),
 ("백마켓, 창업자 출연 파격 광고 캠페인 공개",
  "중고 리퍼비시 전자제품 플랫폼 백마켓이 창업자가 직접 등장해 자사 제품 신뢰도를 강조하는 광고 캠페인을 뉴욕에서 시작했다. TV, VOD, 옥외광고, 소셜, 오디오를 아우르며 이후 미국·영국·유럽으로 확대될 예정이다.",
  "The Drum", "https://www.thedrum.com/news/ad-of-the-day-back-market-s-founder-stakes-his-mother-s-life-on-its-refurbished-tech"),
 ("WPP, 연내 수백 명 감원 전망",
  "세계 최대 광고그룹 WPP가 CEO 신디 로즈의 '엘리베이트28' 전략 일환으로 전체 인력의 약 1%에 해당하는 수백 명을 감원할 것으로 전망된다. VML과 백오피스 인력이 주요 대상이며 2028년까지 5억 파운드 비용 절감이 목표다.",
  "Campaign US", "https://www.campaignlive.com/article/wpp-expected-cut-hundreds-jobs-year/1964562"),
 ("오픈소스 MMM, 비용은 낮췄지만 난도는 그대로",
  "구글 등의 오픈소스 마케팅믹스모델링(MMM) 도구가 기존 15만~50만 달러에 달하던 컨설팅 비용 장벽을 없앴다. 그러나 소프트웨어는 무료가 되었어도 정확한 결과를 위한 데이터 품질과 전문성 요구는 그대로 남아있다.",
  "MarTech", "https://martech.org/open-source-made-mmm-cheaper-not-easier/"),
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
