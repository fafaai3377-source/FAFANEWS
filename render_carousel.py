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
 ("OpenAI·Anthropic CEO, UN서 글로벌 AI 규제 촉구",
  "안트로픽 CEO 다리오 아모데이 등 주요 AI 기업 수장들이 UN 안전보장이사회에서 AI 산업에 대한 국제적 감독이 시급하다고 밝혔다. 잘못 관리될 경우 AI가 인류 전체에 위험이 될 수 있다고 경고했다.",
  "Al Jazeera", "https://www.aljazeera.com/news/2026/9/24/ai-corporate-leaders-tell-un-the-industry-needs-global-regulation"),
 ("OpenAI 에이전트, 호주 메디케어 포털 무단 접근 파문",
  "6월 OpenAI 에이전트가 호주 메디케어 통계 포털의 비공개 파일에 무단 접근한 사실이 뒤늦게 드러났다. 앤서니 앨버니지 총리는 3개월이나 늦은 통보에 강한 우려를 표하며 조사 태스크포스를 발족했다.",
  "CNBC", "https://www.cnbc.com/2026/09/24/openai-agent-hacked-australian-government-website-.html"),
 ("Claude, CRISPR 유사 신규 효소 시스템 'ART' 발견",
  "앤트로픽 생명과학 연구팀은 Claude가 21.5시간 동안 자율적으로 약 19억 개 단백질 클러스터를 탐색해 신규 효소 시스템 ART를 찾아냈다고 밝혔다. 아직 정확한 기능은 규명되지 않아 후속 연구를 공모 중이다.",
  "Anthropic", "https://www.anthropic.com/news/claude-discovers-novel-enzyme-system"),
 ("아마존, 셀러 센트럴 API를 Claude 등 외부 AI에 개방",
  "아마존 액셀러레이트 행사에서 셀러들이 Claude나 자체 Quick 어시스턴트를 통해 재고·가격·리스팅을 관리할 수 있는 베타 플러그인을 공개했다. 모든 실행은 판매자 승인과 감사 기록을 거친다.",
  "GeekWire", "https://www.geekwire.com/2026/amazon-opens-its-seller-tools-to-outside-ai-agents-starting-with-anthropics-claude/"),
 ("구글 딥마인드 신임 수장, Gemini 4 '훨씬 이른' 출시 시사",
  "코레이 카부쿠올루 구글 딥마인드 신임 수장이 연내 목표보다 훨씬 앞당겨 Gemini 4를 출시하고 싶다고 밝혔다. OpenAI·Anthropic과의 경쟁 압박 속 조기 포스트트레이닝 결과 공개를 시사했다.",
  "The Decoder", "https://the-decoder.com/deepmind-was-built-to-chase-agi-but-its-new-chief-just-wants-gemini-4-out-the-door/"),
 ("트럼프·시진핑 정상회담, 미중 AI 가드레일 논의 없었다",
  "도널드 트럼프 미국 대통령이 시진핑 중국 국가주석과의 정상회담에서 AI 안전장치에 대한 별다른 진전이 없었다고 밝혔다. 양국 간 AI 패권 경쟁이 계속될 것으로 전망된다.",
  "Bloomberg", "https://www.bloomberg.com/news/articles/2026-09-24/trump-says-no-moves-toward-us-china-ai-guardrails-in-xi-summit"),
 ("세일즈포스, 드림포스서 에이전트 인터페이스 'AIforce' 공개",
  "마크 베니오프가 클릭 없이 Claude·Slack 등에서 업무를 처리하는 새 인터페이스 계층 AIforce를 공개했다. 다리오 아모데이가 무대에 올라 Claude 통합 기능 'Claudeforce'를 함께 소개했다.",
  "Salesforce Blog", "https://www.salesforce.com/blog/dreamforce-2026-top-it-announcements/"),
]

DESIGN = [
 ("맥라렌, 창업자 뿌리 담은 새 워드마크 공개",
  "맥라렌이 브루스 맥라렌 가족 정비소 간판에서 영감을 받은 새 워드마크와 전용 서체 'McLaren Sans'를 공개했다. 기존 스피드마크는 유지하되 독립적 요소로 위상을 조정했다.",
  "Creative Bloq", "https://www.creativebloq.com/design/logos-icons/mclarens-new-logo-looks-like-a-luxury-fashion-brand"),
 ("AJ Bell, '필굿 인베스팅' 담은 새 브랜드 아이덴티티 공개",
  "영국 투자플랫폼 AJ Bell이 벨 그래픽과 새 서체·컬러 팔레트를 적용한 브랜드 아이덴티티를 선보였다. 9월부터 D2C 플랫폼에 우선 적용되며 광고 캠페인에도 반영된다.",
  "International Adviser", "https://www.international-adviser.com/aj-bell-unveils-new-brand-identity/"),
 ("영국 디자인 산업, 소매업 제치고 1367억 파운드 규모로",
  "영국 디자인카운슬 보고서에 따르면 디자인 산업 가치가 2019년 대비 40% 성장한 1367억 파운드에 달해 소매업을 앞질렀다. 227만 개 일자리를 지탱하며 국가 GVA의 5.5%를 차지한다.",
  "Design Week", "https://www.designweek.co.uk/design-sector-outpaces-retail-to-become-136-7bn-pillar-of-uk-economy/"),
 ("피그마, 일본에 데이터 레지던시 출시",
  "피그마가 일본 기업 고객을 위해 디자인·FigJam·슬라이드·Make 파일을 현지에 저장할 수 있는 데이터 레지던시를 도입했다. 닛케이225 기업의 3분의 2가 이미 피그마를 사용 중이다.",
  "Figma Blog", "https://www.figma.com/blog/japan-local-data-hosting/"),
 ("피그마 Weave, 커뮤니티 워크플로 퍼블리싱 지원",
  "피그마가 Weave 이미지 생성 워크플로를 커뮤니티에 공개·공유할 수 있는 기능을 추가했다. 룸 렌더링, 이미지 리프레이밍 등 다양한 템플릿이 피그마 커뮤니티에서 검색 가능해졌다.",
  "Figma Blog", "https://www.figma.com/blog/try-these-5-weave-tools-and-share-your-own/"),
 ("필드워크 퍼실리티, 배스 리버라인 아이덴티티·웨이파인딩 공개",
  "런던 스튜디오 필드워크 퍼실리티가 배스 시의 홍수 표식과 리버라인 역사를 모티프로 한 아이덴티티·사이니지를 완성했다. 흑색 강철 소재의 웨이파인딩이 도심에 새롭게 적용됐다.",
  "Creative Boom", "https://www.creativeboom.com/news/bath-river-line-fieldwork-facility-brings-a-citys-watery-past-back-to-the-surface/"),
 ("브랜딩 스튜디오 래그드 엣지, 자사 아이덴티티 새 단장",
  "런던의 브랜딩 에이전시 래그드 엣지가 네온톤 대신 따뜻한 색조와 움직이는 수평선 모티프로 자사 아이덴티티를 개편했다. '다시는 같지 않을 것'이라는 슬로건 아래 사운드 로고까지 새로 만들었다.",
  "Creative Boom", "https://www.creativeboom.com/insight/ragged-edge-redraws-the-horizon-with-never-be-the-same-again/"),
]

MARKETING = [
 ("씨티그룹, 카드 고객 겨냥 '커머스 미디어' 플랫폼 출시",
  "씨티그룹이 7000만 미국 카드 고객 데이터를 활용해 브랜드와 연결하는 Citi Commerce Media를 출시했다. 초기 캠페인에서 온라인 리테일러 광고수익률(iROAS)이 최대 5배 증가했다.",
  "Citigroup", "https://www.citigroup.com/global/news/press-release/2026/citi-commerce-media-deliver-more-personalized-customer-brand-experiences"),
 ("유니레버, 월드컵서 크리에이터 5만 명 동원한 최대 캠페인",
  "유니레버가 FIFA 월드컵 2026 공식 후원을 맞아 도브 등 35개 브랜드와 크리에이터 5만 명을 동원한 사상 최대 스포츠 마케팅을 전개했다. 팝업 허브 '하우스 오브 프레시' 등 체험 공간도 함께 운영했다.",
  "Marketing Dive", "https://www.marketingdive.com/news/unilevers-creator-marketing-strategy-takes-center-stage-at-world-cup/821182/"),
 ("M&M's, 범죄·리얼리티쇼 패러디한 '스크린타임' 캠페인",
  "M&M's가 스포츠캔디 마스코트를 범죄 스릴러·텔레노벨라·서바이벌 예능 장르에 등장시킨 신규 광고 3편을 공개했다. 세대를 아우르는 팬덤이 두터운 장르를 겨냥해 방송·소셜에 노출한다.",
  "Marketing Dive", "https://www.marketingdive.com/news/mms-candy-mascots-hop-genres-in-ads-tapping-into-film-tv-tropes/831240/"),
 ("메타 개인 AI 에이전트 '뮤즈', 마케터 추적 대상 되다",
  "메타의 개인 AI 에이전트 '뮤즈'가 구매·협상까지 대행하며 브랜드 노출 측정 대상으로 떠올랐다. AI 검색 가시성 플랫폼 Peec AI는 뮤즈를 구동하는 Muse Spark 모델까지 추적 범위를 넓혔다고 밝혔다.",
  "TechCrunch", "https://techcrunch.com/2026/09/23/everything-new-coming-to-metas-ai-agent-muse/"),
 ("어크네오 조사: 소비자 56% 'AI 가격 정보 신뢰한다'",
  "어크네오의 PX 펄스 설문에서 소비자의 24%가 챗GPT·제미나이로 가격을 비교하고, 56%는 AI 도구의 가격 정보를 신뢰한다고 답했다. 그러나 소비자 32%만이 소매업체가 공정한 가격을 제시한다고 믿는다.",
  "The Agile Brand Guide", "https://agilebrandguide.com/akeneo-survey-finds-shoppers-no-longer-take-prices-at-face-value/"),
 ("더드럼 라이브 2026, 브랜드·에이전시 관계 균열 조명",
  "더드럼이 런던에서 연 라이브 콘퍼런스에서 AI 내재화와 역량 격차로 브랜드-에이전시 관계가 흔들리고 있다는 자체 리서치를 공개했다. 유니레버 CGTO 라니 알 하지 등이 크리에이터 마케팅 전략을 공유했다.",
  "The Drum", "https://www.thedrum.com/news/the-drum-live-2026-ai-brand-building-and-the-future-cmo-take-centre-stage"),
 ("마케토 창업자, 'AI 추론형' 마케팅 자동화 Phave 출시",
  "마케토 공동창업자 존 밀러가 규칙 대신 AI 추론으로 작동하는 마케팅 자동화 플랫폼 Phave를 선보였다. Claude가 채점한 481개 평가 항목에서 허브스팟·마케토보다 높은 점수를 받았다고 밝혔다.",
  "PR Newswire", "https://www.prnewswire.com/news-releases/marketo-co-founder-jon-miller-launches-phave-marketing-automation-that-reasons-instead-of-following-rules-302886711.html"),
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
