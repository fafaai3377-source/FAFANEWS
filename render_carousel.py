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
 ("오픈AI·앤트로픽·구글, 수주간 AI 안전 논의 이어와",
  "TechCrunch에 따르면 오픈AI, 앤트로픽, 구글 세 회사가 몇 주간 AI 안전을 주제로 비공개 논의를 이어온 것으로 확인됐다. 오픈AI 정책 총괄이 이를 처음 공식 확인했다.",
  "TechCrunch", "https://techcrunch.com/2026/09/15/openai-anthropic-google-have-been-in-talks-on-ai-safety-for-weeks/"),
 ("다리오 아모데이, \"AI 개발 속도 늦추자\" 제언",
  "앤트로픽 CEO 다리오 아모데이가 주말 에세이에서 AI 업계에 개발 속도 조절을 촉구했다. 올트먼과 머스크 등 경쟁사 수장들도 공감을 표하며 AI 관련주가 흔들렸다.",
  "Jerusalem Post", "https://www.jpost.com/business-and-innovation/article-908435"),
 ("중국 관영매체, 미국발 'AI 속도조절론' 비판",
  "중국 관영매체가 아모데이발 AI 슬로다운 제안을 자국 우위를 지키려는 자기중심적 주장이라며 정면 반박했다. 미중 AI 경쟁 구도가 다시 부각됐다.",
  "NBC News", "https://www.nbcnews.com/world/china/china-ai-slowdown-trump-amodei-altman-threat-cold-war-rcna597631"),
 ("어질리티 로보틱스, 협업형 휴머노이드 '디지트 5' 공개",
  "어질리티 로보틱스가 사람과 안전 장벽 없이 나란히 일할 수 있는 첫 휴머노이드 '디지트 5'를 공개했다. AI 충돌회피 소프트웨어와 새 센서로 최대 50파운드 하중을 반복 처리한다.",
  "Bloomberg", "https://www.bloomberg.com/news/articles/2026-09-15/agility-robotics-unveils-humanoid-designed-to-work-safely-alongside-humans"),
 ("코넬리스 네트웍스, AI 네트워킹에 2억 500만 달러 조달",
  "인텔에서 분사한 코넬리스 네트웍스가 2억 500만 달러를 유치하고 신규 아키텍처 'Active Compute Fabric'을 공개했다. 엔비디아 대항마로 개방형 AI 클러스터 네트워킹을 내세운다.",
  "TechCrunch", "https://techcrunch.com/2026/09/14/ai-infrastructure-company-cornelis-raises-205m-to-chip-away-at-nvidias-dominance/"),
 ("샌더스·배넌, 워싱턴서 'AI 규제 강화' 한목소리",
  "버니 샌더스 상원의원과 스티브 배넌이 'Pro-Human Assembly' 행사에 나란히 참석해 AI 개발 속도에 대한 의회 차원의 통제를 촉구했다. 좌우 진영을 넘나든 이례적 공동 행보로 주목받았다.",
  "NPR", "https://www.npr.org/2026/09/15/nx-s1-5968678/bernie-sanders-and-steve-bannon-to-share-a-stage-to-promote-curbs-on-ai"),
 ("클라우드플레어, 9월 15일부터 '혼합형' AI 크롤러 차단",
  "클라우드플레어가 9월 15일부터 광고가 붙은 페이지에서 검색·에이전트·학습 크롤러를 분리하지 않은 혼합형 AI 크롤러를 기본 차단하기로 했다. 이 때문에 구글봇 등 통합 크롤러까지 함께 막히는 부작용이 지적된다.",
  "Search Engine Journal", "https://www.searchenginejournal.com/cloudflares-ai-crawler-rules-can-block-googlebot/581385/"),
]

DESIGN = [
 ("캔바, 'Pro Suite' 출시 — 어피니티·캐벌리·플로리시·레오나르도 통합",
  "캔바가 어피니티, 캐벌리, 플로리시, 레오나르도 등을 하나로 묶은 'Pro Suite'를 출시하며 신규 기능 100여 개를 추가했다. 전문 디자이너용 툴 통합에 속도를 내는 모습이다.",
  "Creative Boom", "https://www.creativeboom.com/news/canva-launches-prosuite-bringing-affinity-cavalry-flourish-and-leonardo-together-with-more-than-new-100-features/"),
 ("위위원트모어의 'BRU' 탄산수 리디자인, 패키징의 교과서",
  "디자인 스튜디오 위위원트모어가 탄산수 브랜드 BRU의 병 디자인을 우아하게 재설계했다. 2026년 패키징 디자인이 나아갈 방향을 보여준다는 평가를 받는다.",
  "Creative Boom", "https://www.creativeboom.com/news/what-wewantmores-redesign-of-brus-bottles-can-teach-us-about-packaging-design-in-2026/"),
 ("위키백과, 기능이 미학을 압도한 그래픽 디자인 역사 조명",
  "It's Nice That가 기능이 미학보다 우선하는 백과사전 위키백과의 마스코트와 비주얼 역사를 짚었다. 실용성 중심 디자인이 남긴 독특한 시각 유산을 재조명한다.",
  "It's Nice That", "https://www.itsnicethat.com/features/wikipedia-graphic-design-spotlight-150926"),
 ("영화의 비선형 타임라인에서 영감 얻은 'Ntsal' 아이덴티티",
  "그래픽 디자인 프로젝트 Ntsal이 영화의 비선형적 시간 구조를 모티프로 한 표현적 모션 아이덴티티를 선보였다. 압축과 확장을 오가는 리듬으로 유럽 영화의 파노라마를 담아냈다.",
  "It's Nice That", "https://www.itsnicethat.com/articles/ntsal-panorama-of-the-european-film-graphic-design-project-150926"),
 ("실반 힐레브란트, 125년 역사 렘브란트 물감 브랜드 새 단장",
  "디자이너 실반 힐레브란트가 125년 전통의 유화물감 브랜드 렘브란트에 현대적인 리프레시를 더했다. 헤리티지를 지키면서도 동시대적인 감각을 입혔다.",
  "It's Nice That", "https://www.itsnicethat.com/articles/sylvan-hillebrand-rembrandt-graphic-design-project-140926"),
 ("마더디자인, 심리상담 플랫폼 '킨'에 포크아트 리브랜드",
  "마더디자인이 온라인 심리상담 플랫폼 킨(Keen)에 미니멀한 웰니스 미학 대신 포크아트풍 리브랜드를 적용했다. 정형화된 웰니스 디자인 문법에 반기를 든 사례로 주목받는다.",
  "Creative Boom", "https://www.creativeboom.com/news/mother-design-gives-psychic-platform-keen-a-folk-art-rebrand-that-takes-on-minimalist-wellness/"),
 ("기네스, 로고 안 바꾸고 '현대화'하는 디자인 묘수",
  "기네스가 상징적인 로고를 유지한 채 메시지 디자인만 손봐 브랜드를 현대화하는 전략을 택했다. 무리한 리브랜드 없이도 신선함을 줄 수 있음을 보여준 사례다.",
  "Creative Boom", "https://www.creativeboom.com/news/this-clever-design-trick-by-guinness-means-it-can-modernise-without-any-need-for-a-new-logo/"),
]

MARKETING = [
 ("도스리, 임상 인텐트 신호 기반 'HCP 스위트' 출시",
  "헬스케어 마테크 기업 도스리가 CTV·소셜·프로그래매틱 채널을 하나의 오케스트레이션으로 묶은 'Intent Powered HCP 스위트'를 출시했다. 실시간 임상 인텐트 신호로 제약 광고 타이밍을 정교화한다.",
  "PR Newswire", "https://www.prnewswire.com/news-releases/doceree-launches-intent-powered-hcp-suite-bringing-clinical-intent-signals-to-ctv-social-and-programmatic-302877829.html"),
 ("NP디지털, '핀테크 AI 가시성 지수' 첫 발표",
  "NP디지털이 챗GPT·제미나이·퍼플렉시티 등에서 나온 6만 8천여 개 AI 답변을 분석한 핀테크 AI 가시성 지수를 공개했다. 애플이 1위, 소파이가 순수 핀테크 브랜드 중 최고 점수를 받았다.",
  "MediaPost", "https://www.mediapost.com/publications/article/417750/multi-category-presence-key-to-ai-visibility-for-f.html"),
 ("아조마, AI 답변에 뺏긴 검색 트래픽 되찾을 'GEO 툴' 분석",
  "에이전틱 커머스 최적화 기업 아조마가 브랜드가 AI 답변에 빼앗기는 검색 트래픽을 되찾는 데 도움이 되는 생성엔진최적화(GEO) 툴을 분석해 발표했다. 프롬프트 단위 추적과 인용 출처 분석을 핵심 기준으로 꼽았다.",
  "MarTech Series", "https://martechseries.com/content/generative-engine-optimisation-azoma-on-which-geo-tools-help-brands-losing-search-traffic-to-ai-answers/"),
 ("터키 스타트업 인덱사, AI 브랜드 가시성 측정 툴 출시",
  "이스탄불 스타트업 인덱사가 기업이 AI 시스템 안에서 어떻게 언급되는지 추적하는 브랜드 가시성 툴을 새로 선보였다. 어떤 출처가 인용되는지까지 경쟁사와 비교 분석해준다.",
  "GlobeNewswire", "https://www.globenewswire.com/news-release/2026/09/14/3361606/0/en/indexa-launches-ai-brand-visibility-tools-to-enhance-company-representation-across-ai-systems.html"),
 ("애드위크 브랜드위크 2026, 애틀랜타서 첫 'AI 데이' 연다",
  "애드위크의 연례 행사 브랜드위크가 9월 15일 애틀랜타에서 개막해 코카콜라·스타벅스·크록스 등이 참여한다. 애드위크 사상 처음으로 마케터를 위한 AI 전용 데이가 별도로 마련됐다.",
  "Adweek", "https://www.adweek.com/brand-marketing/brandweek-returns-to-atlanta-with-starbucks-crocs-mattel-coca-cola-and-more/"),
 ("허쉬 크림바 광고, 지하철 세트에서 케이티아이가 열창",
  "마틴 에이전시가 허쉬의 신제품 크림바를 알리기 위해 걸그룹 케이티아이(Katseye)가 만원 지하철에서 노래하는 캠페인을 제작했다. 새 제품 라인의 인지도 확산을 노렸다.",
  "Richmond BizSense", "https://richmondbizsense.com/2026/09/15/the-pitch-advertising-and-marketing-news-for-9-15-26/"),
 ("휘슬스, 50주년 기념 '수상한 전단' 캠페인으로 고객 캐스팅",
  "패션 브랜드 휘슬스가 창립 50주년을 맞아 런던 곳곳에 붙인 미스터리한 전단으로 실제 고객 50명을 찾아 캠페인 주인공으로 세웠다. 브랜드 헤리티지를 고객 스토리로 풀어낸 이색 사례다.",
  "Creative Boom", "https://www.creativeboom.com/news/whistles-marks-50-years-by-casting-50-customers-found-through-mysterious-fly-posters-across-london/"),
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
