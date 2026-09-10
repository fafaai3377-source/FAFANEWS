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
    "어도비": "Adobe",
    "릴로": "Rilo",
    "바자보이스": "Bazaarvoice",
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
 ("OpenAI, 사이버보안 특화 'GPT-6 Astra' 공개",
  "OpenAI가 자사 최고 성능이라고 밝힌 GPT-6 Astra를 공개했다. 수정된 벤치마크에서 인간 개입 없이 제로데이 취약점 2건을 발견·활용해 안전성 논란이 커졌고, 미 상원에서는 첨단 AI 개발 중단을 요구하는 법안이 발의됐다.",
  "Al Jazeera", "https://www.aljazeera.com/economy/2026/9/4/openai-unveils-gpt-6-astra-amid-rising-scrutiny-and-safety"),
 ("Anthropic, Claude Fable 5.1·Mythos 5.1 출시",
  "Anthropic이 범용 모델 Fable 5.1과 사이버방어·생명과학 등 제한된 영역용 Mythos 5.1을 공개했다. 캐시 읽기 비용을 75% 낮췄고 Terminal-Bench-Science 점수도 전작 24.7%에서 52.6%로 올랐다.",
  "VentureBeat", "https://venturebeat.com/technology/anthropics-claude-fable-5-1-and-mythos-5-1-arrive-with-a-75-cost-reduction-for-fable-cache-reads"),
 ("미스트랄, 30억 유로 시리즈D — 유럽 테크 최대 규모",
  "프랑스 AI 스타트업 미스트랄이 밸류에이션 210억 유로에 30억 유로를 조달했다고 밝혔다. 삼성전자가 주도했고 엔비디아·세일즈포스벤처스·블랙록 등이 참여했으며, 2030년까지 유럽에 1기가와트 규모 컴퓨팅 인프라를 구축할 계획이다.",
  "TechCrunch", "https://techcrunch.com/2026/09/08/mistral-raises-e3b-as-sovereign-ai-becomes-big-business/"),
 ("엔비디아, 허깅페이스 약 129억 달러에 인수",
  "엔비디아가 AI 플랫폼 허깅페이스를 약 129억 달러에 인수하는 계약을 체결했다고 밝혔다. 1,800만 명 이상의 개발자와 300만 개 모델을 보유한 허깅페이스는 인수 후에도 개방형 플랫폼으로 계속 운영된다.",
  "NVIDIA Blog", "https://blogs.nvidia.com/blog/nvidia-to-acquire-hugging-face/"),
 ("메타, 크로스앱 개인 AI 에이전트 'Muse' 출시",
  "메타가 이메일·캘린더·결제 등 여러 앱을 넘나들며 작업을 수행하는 개인 AI 에이전트 Muse를 출시했다. 전용 보안 가상머신에서 구동되며 미국 성인 이용자를 대상으로 무료·월 20달러·월 100달러 요금제로 제공된다.",
  "TechCrunch", "https://techcrunch.com/2026/09/08/meta-debuts-its-muse-ai-agent-will-consumers-trust-it/"),
 ("OpenAI, AI 에이전트 1만 개가 '나비에-스토크스' 문제 풀었다 주장",
  "OpenAI는 약 1만 개의 AI 에이전트가 88시간 만에 밀레니엄 난제인 나비에-스토크스 방정식 문제의 해법을 제시했다고 밝혔다. 다만 클레이수학연구소의 독립 검증은 아직 없고 일부 수학자는 결과에 의문을 제기했다.",
  "CNBC", "https://www.cnbc.com/2026/09/09/openai-navier-stokes-math-problem-solved.html"),
 ("미 정부, 중국 AI 기업들 '모델 증류' 대규모 도용 지목",
  "미 국가안보국(NSA)·CISA·FBI가 공동 발표한 경보에서 딥시크·문샷AI·알리바바 등 6개 중국 AI 기업이 2024년 말부터 클로드·GPT·제미나이 등 미국 프런티어 모델을 대규모로 증류(distillation)해왔다고 밝혔다.",
  "The Hacker News", "https://thehackernews.com/2026/09/us-agencies-accuse-china-ai-firms-of.html"),
]

DESIGN = [
 ("테슬라 사이버캡, 화면 속 숨은 조이스틱 인터페이스 노출",
  "핸들·페달이 없는 로보택시 사이버캡 탑승객 영상에서 화면 좌하단에 이동·정지·경적·문 조작용 조이스틱 UI가 노출된 사실이 확인됐다. 디포에서 직원이 저속으로 차량을 옮길 때 쓰는 인터페이스로 추정되며, 오스틴 출시 일주일 만에 승객에게 노출됐다.",
  "UX News", "https://ux-news.com/tesla-cybercab-riders-encounter-virtual-controls-oa-f3fd03/"),
 ("피그마, AI 에이전트가 셰이더·제너러티브 플러그인 편집 가능하게",
  "피그마 MCP 서버가 업데이트돼 외부 AI 코딩 에이전트가 피그마 디자인 안의 제너러티브 플러그인과 셰이더를 확인·수정할 수 있게 됐다. 프레임을 React 코드로 옮겨도 셰이더 효과가 그대로 렌더링되며, 커스텀 툴을 피그마 커뮤니티에 게시하는 기능도 추가됐다.",
  "UX News", "https://ux-news.com/figma-lets-ai-agents-edit-shaders-and-carry-them-into-react/"),
 ("어도비, 비밀 웹 디자인 툴 'Project Oasis' 테스트 중",
  "어도비가 브랜드 인지형 AI를 결합한 브라우저 기반 그래픽 디자인 툴 'Project Oasis'를 개발 중이며, 비공개 베타가 8월 말 시작돼 11월 어도비 맥스까지 이어진다. 기능·가격·출시일은 아직 공개되지 않았다.",
  "Web Designer Depot", "https://webdesignerdepot.com/adobe-has-a-secret-new-design-tool-and-you-can-apply-to-test-it/"),
 ("노르웨이 호스피탈리티 브랜드 Ytri, 새 아이덴티티 공개",
  "디자인 스튜디오 Bielke&Yang이 노르웨이 호스피탈리티 브랜드 Ytri의 이름·로고·아이덴티티를 새로 디자인했다. 독자적인 끌질 느낌의 세리프 서체를 중심으로 미니멀하고 노르딕한 건축적 비주얼 언어를 구축했다.",
  "Brand New", "https://www.underconsideration.com/brandnew/archives/new_name_logo_and_identity_for_ytri_by_bielkeyang.php"),
 ("스튜디오 Form, 영국 사이클링과 스폰서 로이즈의 '두 로고' 문제 해결",
  "런던 스튜디오 Form이 영국 사이클링 내셔널 시리즈·챔피언십의 아이덴티티를 새로 디자인하며, 전통적인 스폰서 로고 병기 대신 커스텀 변형한 Anton 서체 안에 브리티시 사이클링과 로이즈의 비주얼 요소를 함께 녹여냈다.",
  "Creative Boom", "https://www.creativeboom.com/inspiration/how-form-solved-the-classic-two-logo-lockup-problem-for-british-cycling/"),
 ("기네스, 로고 교체 없이 '하프'를 단어 속에 숨긴 캠페인 공개",
  "기네스가 AMV BBDO와 함께 'LoVely' 캠페인을 선보였다. 160년 된 하프 심볼이 'Lovely'라는 단어의 'v' 자리를 대신하는 그래픽으로, 로고 자체를 바꾸지 않고도 브랜드를 새롭게 보이게 했다. 아일랜드·영국을 시작으로 아프리카·아시아태평양까지 확대될 예정이다.",
  "Creative Boom", "https://www.creativeboom.com/inspiration/this-clever-design-trick-by-guinness-means-it-can-modernise-without-any-need-for-a-new-logo/"),
 ("헬싱키 옛 기차공장, 잊혀진 안전마크로 새 아이덴티티 완성",
  "핀란드 에이전시 Hasan & Partners가 헬싱키의 옛 기관차 공장을 개조한 복합문화공간 'The Train Factory'의 브랜드 아이덴티티를 디자인했다. 역사적인 안전 표시를 날카로운 'T' 로고로 재해석하고 핀란드 열차 번호 서체와 붉은 벽돌색 팔레트를 결합했다.",
  "Creative Boom", "https://www.creativeboom.com/inspiration/all-aboard-helsinkis-old-train-factory-new-identity/"),
]

MARKETING = [
 ("캠벨스, 마케팅 예산 85%를 디지털로 전환",
  "캠벨스가 매출 8% 감소 속에서 마케팅 예산의 약 85%를 소셜·인플루언서·이커머스·AI 플랫폼 등 디지털 채널로 재편한다고 밝혔다. 성장 브랜드인 라오스·골드피쉬·페퍼리지팜에 집중 투자할 방침이다.",
  "Marketing Dive", "https://www.marketingdive.com/news/campbells-heats-up-digital-focus-amid-marketing-reformulation/829764/"),
 ("앤트로폴로지, 2026 가을 캠페인 'Fall Is a Feeling' 공개",
  "앤트로폴로지가 가을 컬렉션 출시에 맞춰 'Fall Is a Feeling' 캠페인을 자사 채널·유료 광고·CTV에 걸쳐 선보였다. 캐시미어·스웨이드 등 촉감 중심의 럭셔리함을 강조하며 매장 스타일링 이벤트도 함께 진행한다.",
  "PR Newswire", "https://www.prnewswire.com/news-releases/anthropologie-welcomes-fall-2026-with-fall-is-a-feeling-campaign-302871470.html"),
 ("매그나이트, EMEA 첫 에이전틱 광고 캠페인 집행",
  "매그나이트가 프랑스 트레이딩데스크 Amnet France와 함께 EMEA 최초의 에이전틱 광고 캠페인을 집행했다. AI 바이어 에이전트가 자연어 지시만으로 CTV 캠페인을 구성·운영해 셋업 시간을 약 70% 단축하고 95%의 영상 완주율을 기록했다.",
  "GlobeNewswire", "https://www.globenewswire.com/news-release/2026/09/08/3357336/0/en/magnite-launches-first-agentic-campaign-in-emea-with-amnet-france.html"),
 ("펩시코, 글로벌 미디어 대행을 퍼블리시스로 이관",
  "펩시코가 20여 년간 이어온 옴니콤(OMD)과의 관계를 끝내고 글로벌 미디어 계정을 퍼블리시스 그룹으로 넘겼다. 퍼블리시스는 'One PepsiCo' 글로벌 미디어 운영 모델을 구축하며, 대신 코카콜라 글로벌 미디어 입찰전에서는 발을 뺄 것으로 알려졌다.",
  "Marketing Dive", "https://www.marketingdive.com/news/pepsico-hands-global-media-to-publicis-amid-transformation-at-cpg-giant/829556/"),
 ("어도비, 인도 AI 자동화 스타트업 릴로 인수",
  "어도비가 텍스트 지시만으로 여러 단계의 마케팅 워크플로를 실행하는 AI 자동화 스타트업 릴로(Rilo)를 인수했다. 약 1.9억 달러 규모였던 Semrush 인수 4개월 만의 후속 딜로, 어도비의 에이전틱 마케팅 툴 확장을 가속한다.",
  "TechCrunch", "https://techcrunch.com/2026/09/02/adobe-acquires-indian-market-intelligence-startup-rilo/"),
 ("바자보이스, AI 쇼핑 플랫폼 노출 높이는 'AI 비저빌리티' 출시",
  "바자보이스가 리뷰·평점·UGC 사진을 AI가 읽을 수 있는 구조화 데이터로 변환해 AI 검색·쇼핑 도구에 노출시키는 'AI 비저빌리티' 패키지를 출시했다. 해당 인프라를 쓴 브랜드는 AI발 유입 트래픽이 중앙값 기준 40% 늘었다고 밝혔다.",
  "GlobeNewswire", "https://www.globenewswire.com/news-release/2026/09/01/3354231/19098/en/bazaarvoice-introduces-ai-visibility-package-to-prime-brand-content-and-ugc-for-ai-recommendation.html"),
 ("허쉬, 케이트아이와 손잡고 Z세대 겨냥 신제품 캠페인",
  "허쉬가 올해 최대 규모 신제품인 아포가토·솔티드카라멜 크림바를 빌보드 1위 그룹 케이트아이(Katseye)와 함께 선보였다. 지하철에서 퍼포먼스를 펼치는 60초 광고를 중심으로 TV·디지털·옥외광고를 2027년까지 이어간다.",
  "Marketing Dive", "https://www.marketingdive.com/news/how-hersheys-katseye-campaign-takes-on-gen-zs-desire-for-indulgence/829013/"),
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
