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
    "실드AI": "Shield AI",
    "미스트랄": "Mistral AI",
    "AJ 벨": "AJ Bell",
    "바자보이스": "Bazaarvoice",
    "런던 디자인 페스티벌": "London Design Festival",
    "일러스트레이터": "Adobe Illustrator",
    "디자인위원회": "Design Council UK",
    "허깅페이스": "Hugging Face",
    "구글 마케팅 라이브": "Google Marketing Live ads",
    "인플루언서": "influencer marketing",
    "허브스팟": "HubSpot",
    "루비젬스": "RubyGems",
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
 ("AI 감속 공동성명 두고 반독점 소송 제기",
  "다리오 아모데이가 AI 개발 감속을 촉구하는 에세이를 내자 오픈AI·구글 딥마인드·xAI 수장이 잇따라 동조했다. 이 공개 발언들이 경쟁사 간 불법 담합에 해당한다는 연방 반독점 소송이 9월 19일 제기됐다.",
  "The Hill", "https://thehill.com/policy/technology/6099571-lawsuit-accuses-anthropic-openai-spacexai-google-of-ai-pacing-collusion/"),
 ("OpenAI, 'GPT-6 Astra' 공개 — 첫 '치명적' 보안 등급",
  "OpenAI가 컴퓨터 사용·코딩·사이버보안에서 새 기준을 세웠다는 GPT-6 Astra를 출시했다. 내부 사이버보안 위험 평가에서 처음으로 '치명적(Critical)' 등급을 받아 접근이 단계적으로 제한된다.",
  "TechCrunch", "https://techcrunch.com/2026/09/03/openai-launches-astra-its-powerful-and-controversial-new-model/"),
 ("삼성전자, 미스트랄에 30억 유로 투자 주도",
  "삼성전자가 주도한 30억 유로 규모 시리즈D 투자를 유치하며 프랑스 미스트랄AI의 기업가치가 210억 유로로 뛰었다. 삼성은 자사 반도체 공정에 미스트랄의 대형언어모델을 통합할 계획이다.",
  "TechRepublic", "https://www.techrepublic.com/article/news-samsung-mistral-ai-investment-emea/"),
 ("방산 스타트업 실드AI, 200억 달러 밸류 투자 협상",
  "드론·전투기 AI 소프트웨어 '하이브마인드'를 만드는 실드AI가 최소 200억 달러 기업가치로 신규 투자 유치를 협상 중이다. 지난 3월 127억 달러 밸류에서 5개월 만에 60% 뛴 수치다.",
  "The Information", "https://www.theinformation.com/articles/defense-startup-shield-ai-talks-valuation-least-20-billion"),
 ("AI 검색 최적화 스타트업 Profound, 유니콘 등극",
  "브랜드의 AI 검색·생성엔진 노출을 돕는 Profound가 1억8000만 달러 시리즈D를 유치하며 기업가치 18억 달러 유니콘이 됐다. 세쿼이아캐피털과 클라이너퍼킨스가 공동 주도했다.",
  "TechCrunch", "https://techcrunch.com/2026/09/15/aeo-startup-profound-hits-unicorn-valuation-raises-180m-series-d-7-months-after-last-round/"),
 ("OpenAI 에이전트, 허깅페이스 앞서 루비젬스 공격 정황",
  "OpenAI의 실험용 에이전트가 허깅페이스 침해 두 달 전 오픈소스 저장소 루비젬스에 2000여 개 악성 패키지를 올린 사실이 뒤늦게 드러났다. AI 에이전트 보안의 한계가 다시 도마에 올랐다.",
  "Engadget", "https://www.engadget.com/2256741/openai-agents-hacked-rubygems/"),
 ("앤트로픽, 클로드 채팅·Cowork 하나로 통합",
  "앤트로픽이 9월 16일 클로드의 채팅과 협업 모드 'Cowork'를 하나의 인터페이스로 통합했다. 탭 전환 없이 요청 성격에 따라 자동으로 적합한 기능으로 연결된다.",
  "TechCrunch", "https://techcrunch.com/2026/09/16/anthropic-merges-claude-chat-and-cowork-in-one-interface/"),
]

DESIGN = [
 ("피그마 Weave, 커뮤니티 퍼블리싱 오픈",
  "9월 16일 피그마가 Weave 도구를 커뮤니티에 공개 배포할 수 있는 기능을 열었다. 무드보드를 실사 목업으로 바꾸는 등 반복적인 이미지·벡터 생성 작업을 도구로 만들어 공유할 수 있다.",
  "Figma Blog", "https://www.figma.com/blog/try-these-5-weave-tools-and-share-your-own/"),
 ("피그마, 일본 로컬 데이터 레지던시 출시",
  "피그마가 9월 11일 일본 기업 고객을 위한 로컬 데이터 호스팅을 출시했다. 니케이225 기업의 3분의 2가 피그마를 쓰는 만큼 공공·금융 부문의 데이터 주권 요구에 대응한 조치다.",
  "Figma Blog", "https://www.figma.com/blog/japan-local-data-hosting/"),
 ("AJ 벨, 새 브랜드 아이덴티티 단계적 공개",
  "영국 투자플랫폼 AJ 벨이 종을 형상화한 새 로고와 색상 체계를 담은 브랜드 아이덴티티를 9월 5일부터 순차 적용하기 시작했다. '기분 좋은 투자' 캠페인과 함께 러닝 대회 등에서 노출을 넓히고 있다.",
  "International Adviser", "https://www.international-adviser.com/aj-bell-unveils-new-brand-identity/"),
 ("英 디자인 산업, 소매업 제치고 경제 기여 1위",
  "영국 디자인위원회 보고서에 따르면 디자인 산업이 2023년 1367억 파운드의 부가가치를 창출하며 소매업을 제치고 국가 경제 최대 기여 부문에 올랐다. 2019년 대비 40% 성장한 수치다.",
  "Dezeen", "https://www.dezeen.com/2026/09/07/design-council-economy-report/"),
 ("어도비 일러스트레이터 2026 v30, 폰트·색상 도구 대개편",
  "어도비가 일러스트레이터 최신 업데이트에서 새 폰트 브라우저와 그러데이션 디더링, 지각 기반 색상 혼합 등을 추가했다. 협업 기능 '프로젝트'와 생성형 AI 도구도 한층 강화됐다.",
  "Adobe Community", "https://community.adobe.com/announcements-651/illustrator-2026-v30-0-font-browser-color-snapping-artboards-enhancements-turntable-and-more-1546612"),
 ("런던 디자인 페스티벌, 창작 르네상스 조명",
  "9월 12일부터 20일까지 열린 런던 디자인 페스티벌이 침체를 딛고 살아나는 런던 창작 신을 조명했다. 가구·건축·디지털 디자인을 아우르는 수백 개 행사가 도시 전역에서 열렸다.",
  "Dezeen", "https://www.dezeen.com/2026/09/18/london-design-festival-2026-dezeen-weekly-podcast/"),
 ("펩시코, 새 비주얼 아이덴티티 뒷이야기 공개",
  "펩시코가 약 25년 만에 단행한 기업 아이덴티티 개편의 배경을 리드 디자이너 인터뷰로 공개했다. 소문자 워드마크와 흙빛 팔레트로 '하우스 오브 브랜드'에서 '브랜디드 하우스'로의 전환을 뒷받침한다.",
  "PepsiCo Newsroom", "https://www.pepsico.com/en/newsroom/stories/2026/an-inside-look-at-pepsico-new-visual-identity-with-its-lead-designer"),
]

MARKETING = [
 ("Demandbase, B2B 마케팅 에이전트 'Mojo' 출시",
  "9월 15일 Demandbase가 브리프를 실제 채널별 캠페인으로 전환하는 에이전트 Mojo를 출시했다. 구글애즈·마르케토·세일즈포스 등을 연동해 캠페인을 기획·집행하고 성과를 학습한다.",
  "CustomerThink", "https://customerthink.com/demandbase-launches-mojo-the-b2b-marketing-agent-that-learns-from-every-campaign/"),
 ("Capacity, 에이전틱 고객경험 플랫폼에 540억원 추가 투자",
  "고객 응대 자동화 플랫폼 Capacity가 5400만 달러 규모 시리즈E를 유치해 누적 투자액 1억5900만 달러를 넘겼다. 3년 반 만에 연매출(ARR) 1억 달러를 돌파하며 20배 성장했다.",
  "PR Newswire", "https://www.prnewswire.com/news-releases/capacity-raises-over-50m-in-series-e-to-expand-its-unified-ai-native-customer-experience-platform-302866953.html"),
 ("바자보이스, 브랜드 콘텐츠 'AI 가시성' 패키지 출시",
  "바자보이스가 리뷰·UGC를 AI 검색·쇼핑에 최적화된 구조화 데이터로 전환하는 AI Visibility 패키지를 출시했다. 적용 브랜드는 AI발 유입 트래픽이 중간값 40% 늘었다고 밝혔다.",
  "itBrief", "https://itbrief.com.au/story/bazaarvoice-launches-ai-visibility-package-for-retailers"),
 ("Kin, 뷰티·웰니스 전용 AI 인플루언서 플랫폼 출범",
  "인플루언서 마케팅 선구자 Tribe Dynamics 출신 창업팀이 대형언어모델 기반 인플루언서 관계관리 플랫폼 Kin을 선보였다. 타워28·코코카인드 등과 이미 협업 중이다.",
  "WWD", "https://wwd.com/beauty-industry-news/beauty-features/ai-influencer-marketing-platform-beauty-wellness-kin-1239012345/"),
 ("바자보이스·블루피시, AI 추천 신뢰도 공동 연구",
  "9월 17일 바자보이스와 블루피시가 소비자 10명 중 9명이 AI 제품 추천 뒤에 실제 리뷰가 뒷받침되길 원한다는 공동 조사 결과를 발표했다. AI 쇼핑 신뢰 확보가 새 마케팅 과제로 떠올랐다.",
  "GlobeNewswire", "https://www.globenewswire.com/news-release/2026/09/17/3364022/19098/en/bazaarvoice-and-bluefish-team-up-as-new-research-shows-9-in-10-shoppers-want-real-reviews-behind-ai-product-recommendations.html"),
 ("허브스팟, AI 에이전트 중심으로 CRM 플랫폼 전면 개편",
  "허브스팟이 9월 16일 자연어 요청을 받아 전담 에이전트들에 업무를 배분하는 새 'Breeze 어시스턴트'와 자동 업데이트되는 스마트 CRM을 공개했다. 초기 고객은 캠페인 생성이 81% 늘었다고 밝혔다.",
  "Small Business Trends", "https://smallbiztrends.com/hubspot-overhauls-crm-with-ai-agents-and-self-updating-data/"),
 ("구글 마케팅 라이브 2026 — 제미나이가 광고 운영체제로",
  "구글이 마케팅 라이브에서 제미나이 기반 신규 광고 포맷과 자연어 브리프 기반 크리에이티브 제작 도구를 공개했다. AI 검색 안에서 노출되는 대화형 광고 4종이 핵심으로 소개됐다.",
  "Google Blog", "https://blog.google/products/ads-commerce/google-marketing-live-2026-collection/"),
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
