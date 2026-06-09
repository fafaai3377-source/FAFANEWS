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
 ("Apple WWDC 2026: 새 Siri와 iOS 27 공개",
  "애플이 6월 8일 WWDC에서 iOS 27과 개선된 Siri를 발표했다. 앱 컨텍스트를 이해하고 자연스러운 대화가 가능해졌으며, 음성·텍스트·이미지를 처리하는 Apple Foundation Models 2세대도 함께 공개됐다.",
  "CNBC", "https://www.cnbc.com/2026/06/08/apple-wwdc-2026-live-updates.html"),
 ("Anthropic, IPO S-1 비공개 제출",
  "Anthropic이 6월 1일 SEC에 S-1 초안을 비공개 제출해 IPO 절차를 공식 시작했다. 직전 시리즈 H 라운드(9,650억 달러 기업가치) 마감 4일 만으로, 역대 최대 규모 AI IPO가 될 전망이다.",
  "TechCrunch", "https://techcrunch.com/2026/06/01/anthropic-files-to-go-public/"),
 ("마이크로소프트 Build 2026: MAI 7개 모델 공개",
  "마이크로소프트가 Build 2026에서 추론·코딩·이미지·음성 등 7종의 MAI 모델을 공개했다. OpenAI 의존도 탈피를 선언하며, MAI-Code-1-Flash는 SWE-bench Pro 51%로 경쟁 모델을 압도했다.",
  "Microsoft AI", "https://microsoft.ai/news/building-a-hillclimbing-machine-launching-seven-new-mai-models/"),
 ("엔비디아 Nemotron 3 Ultra 550B 출시",
  "엔비디아가 6월 4일 550B 파라미터(활성 55B)의 개방형 추론 모델 Nemotron 3 Ultra를 공개했다. Computex 2026에서 발표된 이 모델은 하이브리드 Mamba-Transformer MoE 구조로, 미국 오픈 가중치 모델 중 최고 지능 지수를 달성했다.",
  "MarkTechPost", "https://www.marktechpost.com/2026/06/04/nvidia-ai-releases-nemotron-3-ultra-an-open-550b-mixture-of-experts-hybrid-mamba-transformer-for-long-running-agents/"),
 ("알파벳, AI 인프라에 800억 달러 조달",
  "알파벳이 6월 1일 AI 컴퓨트 인프라 확충을 위한 800억 달러 규모 주식 발행을 발표했다. 버크셔 해서웨이가 100억 달러를 사모로 투자하고, 2026년 총 설비투자는 최대 1,900억 달러에 달할 전망이다.",
  "CNBC", "https://www.cnbc.com/2026/06/01/alphabet-to-raise-80-billion-from-stock-sales-to-fund-ai-buildout.html"),
 ("ChatGPT Dreaming V3: 메모리 2배 강화",
  "OpenAI가 과거 대화를 자동 학습해 사용자 맥락을 축적하는 'Dreaming V3' 메모리 시스템을 출시했다. Plus·Pro 구독자 대상으로 메모리 용량이 2배로 늘어났으며, 백그라운드에서 자동으로 실행된다.",
  "Build Fast with AI", "https://www.buildfastwithai.com/blogs/ai-news-today-june-7-2026"),
 ("구글 I/O 2026: 검색에 AI 에이전트 전면 도입",
  "구글이 I/O 2026에서 Gemini 기반 AI 에이전트를 검색에 전면 통합했다. 에이전트가 멀티스텝 작업을 직접 처리하고 검색 결과를 맥락에 맞게 요약해 사용자가 즉시 행동할 수 있게 지원한다.",
  "Google Blog", "https://blog.google/products-and-platforms/products/search/search-io-2026/"),
]

DESIGN = [
 ("Apple Liquid Glass 디자인, WWDC 2026서 개선",
  "애플이 WWDC 2026에서 지난해 도입한 Liquid Glass 디자인 언어를 사용자 피드백을 반영해 개선했다. 투명도 조절 슬라이더가 추가됐고, 대비와 가독성이 향상됐으며 iOS 27에서는 탐색 탭 구조가 원복됐다.",
  "SiliconANGLE", "https://siliconangle.com/2026/06/08/apple-refines-liquid-glass-design-expands-child-safety-tools-wwdc/"),
 ("피그마 Make, 로컬 코드베이스 연결 베타 공개",
  "피그마 Make가 로컬 코드베이스와 직접 연결되는 기능을 베타로 공개했다. 특정 요소를 지정해 프롬프트하거나 편집 패널·채팅으로 변경을 지시하면 AI 코딩 에이전트가 코드를 수정하고 PR까지 자동 생성한다.",
  "Figma Release Notes", "https://www.figma.com/release-notes/"),
 ("펩시코, 25년 만의 새 비주얼 아이덴티티",
  "펩시코가 약 25년 만에 기업 아이덴티티를 전면 개편했다. 소문자 워드마크와 흙빛 컬러 팔레트, 단순화된 아이코노그래피를 도입하고 'P'를 아이덴티티 중심에 배치해 글로벌 브랜드 일관성을 높였다.",
  "PepsiCo", "https://www.pepsico.com/newsroom/stories/2026/an-inside-look-at-pepsico-new-visual-identity-with-its-lead-designer"),
 ("크래커 배럴 로고, 6일 만에 원복",
  "미국 레스토랑 체인 크래커 배럴이 새 텍스트 전용 로고를 발표한 지 6일 만에 원래 '올드 타이머' 마스코트로 복귀했다. 소셜미디어 역풍과 주가 급락, 정치적 압박이 겹치며 CEO가 공개 사과하고 원복을 결정했다.",
  "eMarketer", "https://www.emarketer.com/content/rebrand-reversal--cracker-barrel-s-6-day-identity-crisis"),
 ("2026년 로고 트렌드: 키네틱·불완전주의 부상",
  "Creative Bloq에 따르면 2026년 로고 디자인은 움직이는 키네틱 로고, 불완전주의, 그리고 플랫 디자인을 벗어난 역동성이 주류로 부상했다. AI 획일화에 맞서 개성과 표현력을 강조하는 적응형 아이덴티티가 새로운 기준으로 자리 잡고 있다.",
  "Creative Bloq", "https://www.creativebloq.com/design/logos-icons/these-logo-design-trends-will-define-2026"),
 ("\"이상함이 브랜딩을 구한다\" 2026 트렌드",
  "Creative Boom은 2026년 브랜딩 키워드로 '이상함(weird)'을 제시했다. AI가 디자인을 균일화하는 흐름에 맞서 독특하고 예상치 못한 브랜드 표현이 소비자 시선을 사로잡는 핵심 차별화 전략으로 주목받고 있다.",
  "Creative Boom", "https://www.creativeboom.com/insight/how-being-weird-can-save-branding-in-2026/"),
 ("캔버 2026 트렌드: '불완전의 미학' 선정",
  "캔버가 2026년 디자인 트렌드 리포트에서 '불완전의 미학(Imperfect by Design)'을 핵심 키워드로 선정했다. 손그림 타이포그래피·유기적 텍스처·진정성 있는 사진이 AI 생성 이미지의 매끄러움에 반하는 인간적 감성으로 각광받고 있다.",
  "Canva Newsroom", "https://www.canva.com/newsroom/news/design-trends-2026/"),
]

MARKETING = [
 ("메타, 2026년 구글 제치고 글로벌 광고 1위",
  "eMarketer는 메타가 2026년 구글을 제치고 디지털 광고 매출 글로벌 1위를 달성할 것으로 전망했다. Advantage+ AI 자동화와 WhatsApp·Threads 신규 광고 지면이 24.1% 성장을 이끌며 역사적 순위 역전이 이뤄지고 있다.",
  "eMarketer", "https://www.emarketer.com/learningcenter/guides/meta-to-surpass-google-in-digital-ad-revenues-for-first-time-ever/"),
 ("ChatGPT 광고, 영국으로 첫 해외 확장",
  "OpenAI가 ChatGPT 광고를 영국에 출시하며 해외 시장 확장을 시작했다. 광고는 무료·Go 플랜 이용자에게만 노출되며, Dentsu·Omnicom·Publicis·WPP 등 글로벌 4대 에이전시가 론칭 파트너로 참여했다.",
  "OpenAI", "https://openai.com/index/new-ways-to-buy-chatgpt-ads/"),
 ("인스타그램, 'AI 크리에이터' 계정 라벨 도입",
  "인스타그램이 5월 4일 AI 콘텐츠 제작자를 위한 선택형 'AI 크리에이터' 계정 라벨을 공개했다. 바이오와 모든 게시물·릴에 라벨이 표시되며, 콘텐츠 노출 순위에는 영향을 주지 않는다고 밝혔다.",
  "Social Media Today", "https://www.socialmediatoday.com/news/instagram-adds-ai-creator-labels/819267/"),
 ("바셀린 × TikTok: 소셜 리스닝 마케팅 성공",
  "유니레버의 바셀린이 전통 광고를 줄이고 TikTok 소셜 리스닝과 크리에이터 주도 콘텐츠로 전략을 전환해 큰 성과를 거뒀다. #VaselineVerified 캠페인은 1억 3,600만 뷰를 기록하고 칸 라이온즈에서 티타늄 라이언 포함 9개 상을 수상했다.",
  "Marketing Dive", "https://www.marketingdive.com/news/inside-vaselines-social-first-innovation-led-marketing-playbook/805872/"),
 ("칸 라이온즈 2026: '크리에이티브 브랜드 라이언' 신설",
  "칸 라이온즈 2026(6월 22~26일)이 단일 캠페인이 아닌 지속적 창의 문화를 구축하는 브랜드 시스템을 평가하는 '크리에이티브 브랜드 라이언'을 신설했다. 브랜드 창의성의 새로운 기준을 제시하는 변화로 주목받고 있다.",
  "Cannes Lions", "https://www.canneslions.com/news/cannes-lions-introduces-the-creative-brand-lion"),
 ("디지털 마케팅 트렌드 2026년 6월: AI 검색 최적화",
  "6월 디지털 마케팅의 핵심 이슈로 ChatGPT 광고, 메타 Advantage+, AI 검색 최적화(AEO)가 부상했다. 퍼스트파티 데이터 활용과 연결된 TV(CTV) 광고 지출이 빠르게 증가하며 전통적인 SEO 중심 전략의 전환을 요구하고 있다.",
  "Two Octobers", "https://twooctobers.com/blog/digital-marketing-updates-june-2026/"),
 ("구글 마케팅 라이브 2026: AI 광고 에이전트 시대",
  "구글이 마케팅 라이브 2026에서 Gemini 기반 AI 광고 에이전트를 전면 공개했다. 캠페인 기획부터 크리에이티브 생성·최적화까지 자동화하며, 퍼포먼스 맥스와 유튜브 광고 전반에 걸쳐 적용된다.",
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
