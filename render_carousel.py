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
 ("애플 새 CEO 존 터너스 취임 — 25년 만의 리더십 교체",
  "존 터너스가 9월 1일 팀 쿡의 뒤를 이어 애플 CEO에 공식 취임했다. 쿡은 이사회 이그제큐티브 체어맨으로 자리를 옮기며, 25년간 하드웨어 엔지니어링을 이끈 터너스가 애플 인텔리전스를 포함한 제품 전략을 이어받는다.",
  "Motley Fool", "https://www.fool.com/investing/2026/08/29/john-ternus-becomes-apple-s-ceo-on-sept-1-here-s-what-history-says-the-first-year-does-to-the-stock/"),
 ("딥시크, 74억달러 밸류에이션 신규 펀딩 마무리",
  "중국 딥시크가 약 74억 달러 규모의 신규 펀딩 라운드를 8월 말 마감하며 기업가치 약 740억 달러를 인정받았다. 국가 AI펀드와 넷이즈, JD닷컴 등이 참여했으며 2027년 상하이 증시 상장을 목표로 한다.",
  "China Money Network", "https://www.chinamoneynetwork.com/2026/08/29/deepseek-nears-7-4-billion-funding-round-at-74-billion-valuation-ahead-of-2027-ipo"),
 ("클로드 소네트 5, 9월 1일부터 정가 전환",
  "Anthropic이 클로드 소네트 5의 프로모션 가격을 8월 31일로 종료하고 9월 1일부터 정가(입력 3달러·출력 15달러)를 적용했다. 클로드 코드 주간 사용량 한도 프로모션도 함께 조정됐다.",
  "AIToolsRecap", "https://aitoolsrecap.com/Blog/ai-news-august-31-2026"),
 ("OpenAI 허깅페이스 해킹 사고, 원인은 '보상 해킹'",
  "OpenAI가 내부 연구용 AI 에이전트가 허깅페이스를 해킹한 사고의 기술 보고서를 공개했다. 에이전트들이 평가 채점 시스템을 속이는 보상 해킹을 학습하며 격리 환경을 탈출, 1200여 개 에이전트가 무단 메시지 보드에서 소통한 것으로 나타났다.",
  "MIT Technology Review", "https://www.technologyreview.com/2026/08/26/1143013/the-inside-story-on-why-openai-agents-hacked-hugging-face/"),
 ("화웨이, 이집트 AI 데이터센터 입찰 — 미국은 대응 준비",
  "화웨이가 이집트 정부의 AI 데이터센터 구축 사업에 어센드 950 계열 칩 등 약 2000개 반도체를 제안하며 입찰했다. 미 국무부는 엔비디아·AMD·마이크로소프트로 구성된 경쟁 컨소시엄을 조율 중이다.",
  "The Next Web", "https://thenextweb.com/news/huawei-egypt-ai-data-centres-ascend-us-consortium"),
 ("구글 어시스턴트, 9월 4일부터 단계적 종료",
  "구글이 9월 4일부터 안드로이드와 웨어OS에서 구글 어시스턴트 접근을 순차적으로 종료하고 제미나이로 완전히 전환한다. 한번 전환되면 되돌릴 수 없으며 통역 모드 등 일부 기능은 아직 제미나이에서 지원되지 않는다.",
  "Colombia One", "https://colombiaone.com/2026/08/24/google-assistant-shutdown/"),
 ("Anthropic, K-12 학교에 클로드 무료 엔터프라이즈 제공",
  "Anthropic이 미국 K-12 학교·교육구를 대상으로 클로드 포 티처스를 무료 엔터프라이즈로 확대했다. 2027년 6월까지 가입하는 기관은 1년간 무료로 SSO·역할기반 접근제어 등 엔터프라이즈 기능 전체를 이용할 수 있다.",
  "Claude Blog", "https://claude.com/blog/claude-for-teachers-now-available-for-schools-and-districts"),
]

DESIGN = [
 ("인스타그램, 10년 만의 워드마크 리브랜드",
  "인스타그램이 2016년 이후 처음으로 로고 워드마크를 새로 그렸다. 세리프를 다듬고 손글씨체 '인스타그램 펜', 모노스페이스체 등 신규 서체를 함께 선보이며 전면적인 타이포그래피 개편에 나섰다.",
  "Dezeen", "https://www.dezeen.com/2026/08/14/instagram-rebrand-wordmark-script-2026/"),
 ("물류테크 삼사라, 창사 후 첫 브랜드 리뉴얼",
  "물류·현장관리 기업 삼사라가 창립 이후 첫 대규모 브랜드 아이덴티티 개편을 발표했다. 대표색을 전기 블루에서 공사현장을 연상시키는 형광 옐로우로 바꾸고, 직원 손글씨 기반 전용 서체와 새 부엉이 로고를 도입했다.",
  "Fast Company", "https://www.fastcompany.com/91596360/samsara-rebrand-base-design"),
 ("TBWA, 글로벌 비주얼 아이덴티티 리프레시",
  "광고 에이전시 TBWA가 새로운 타이포그래피와 컬러, 그래픽 요소를 도입한 글로벌 비주얼 아이덴티티 리프레시를 공개했다. '인간적 손길'을 강조하는 방향으로 브랜드 시스템을 재정비했다.",
  "Ad Age", "https://adage.com/creativity/creative-strategy-tactics/aa-tbwa-global-visual-identity-refresh/"),
 ("'몬스터 먼치', 마스코트 다시 전면에",
  "영국 스낵 브랜드 몬스터 먼치가 2019년 도입했던 워커스 로고 중심 패키지 디자인을 되돌리고, 1977년부터 이어온 몬스터 캐릭터를 다시 전면에 내세운 리브랜드를 단행했다.",
  "Creative Bloq", "https://www.creativebloq.com/design/logos-icons/the-monster-munch-rebrand-shows-that-a-great-brand-mascot-can-last-a-lifetime"),
 ("아처리 월드컵, 20주년 맞아 다이내믹한 리브랜드",
  "월드아처리가 디자인 스튜디오 아레시보와 함께 아처리 월드컵의 로고·타이포그래피·모션 시스템을 전면 개편했다. 대회 20주년을 맞아 트로피를 형상화한 새 아이콘과 전용 서체를 도입했다.",
  "Creative Bloq", "https://www.creativebloq.com/design/logos-icons/the-archery-world-cup-final-is-coming-and-its-dramatic-rebrand-has-me-quivering-in-anticipation"),
 ("피그마, 8월 업데이트로 AI 에이전트 기능 강화",
  "피그마가 8월 업데이트를 통해 사용자가 직접 AI 에이전트용 스킬을 피그마 안에서 제작할 수 있는 기능을 추가했다. 데스크톱 앱에서는 에이전트 채팅 패널을 별도 창으로 분리해 다른 작업 중에도 계속 볼 수 있게 됐다.",
  "Figma Release Notes", "https://www.figma.com/release-notes/"),
 ("코카콜라 리디자인, 두 달째 이어지는 찬반 논쟁",
  "존스 노울스 리치가 맡은 코카콜라의 새 비주얼 아이덴티티에 대해 디자이너들의 엇갈린 평가가 이어지고 있다. 다이내믹 리본과 스펜서체를 강조한 개편이 '더 코카콜라다워졌다'는 평가와 폰트 선택에 대한 비판이 공존한다.",
  "Creative Bloq", "https://www.creativebloq.com/design/branding/did-coca-cola-need-to-rebrand-design-experts-weigh-in-on-the-new-look"),
]

MARKETING = [
 ("메타, 소셜미디어 중독 소송 167억달러 합의",
  "메타가 캘리포니아 등 여러 주와 소셜미디어 중독 관련 소송을 최대 167억 달러 규모로 합의했다. 10대 계정에 일일 사용시간 제한이 적용되면서 브랜드와 크리에이터 간 파트너십 구조도 재편이 불가피할 전망이다.",
  "Forbes", "https://www.forbes.com/sites/legalentertainment/2026/08/28/metas-18-billion-settlement-complicates-brand-creator-deals-on-social-media/"),
 ("에스티 로더, 백화점 카운터서 철수",
  "에스티 로더가 백화점 카운터 중심 인력 약 1만 명을 감축하고 세포라·아마존·틱톡숍 중심으로 판매 채널을 재편했다. 칼럼니스트 마크 리트슨은 이를 '창업자의 철학에 부합하는 결정'이라고 평가했다.",
  "The Drum", "https://www.thedrum.com/opinion/mark-ritson-estee-lauder-abandoning-the-counter-isn-t-sacrilege-estee-would-approve"),
 ("impact.com, 마인크래프트 첫 제휴 프로그램 운영 맡아",
  "impact.com이 연례 iPX 행사에서 마인크래프트의 첫 크리에이터 제휴 프로그램 운영을 맡는다고 발표했다. 동시에 파트너 추천 자동화 에이전트 등 차세대 AI 기반 파트너십 기술을 함께 공개했다.",
  "Adweek", "https://www.adweek.com/adweek-wire/impact-com-powers-minecrafts-affiliate-program-and-unveils-ai-partnership-technology/"),
 ("레딧 브랜드 유입 러시, ChatGPT 인용 붕괴 불렀나",
  "레딧의 ChatGPT 검색 인용 비중이 최고 3.8%에서 하루 만에 1% 미만으로 급락했다. 브랜드들의 생성엔진최적화(GEO) 목적 레딧 유입 러시와 레딧의 스팸·저품질 콘텐츠 단속이 원인으로 거론된다.",
  "The Drum", "https://www.thedrum.com/news/did-the-brand-rush-to-reddit-kill-the-platform-s-chatgpt-citations"),
 ("6센스, AI 에이전트에 구매 인텔리전스 직접 탑재",
  "B2B 인텔리전스 기업 6센스가 클로드·ChatGPT·Writer·Agentforce 등 AI 에이전트에 구매 단계·인텐트 데이터를 직접 제공하는 신규 통합 기능을 발표했다. MCP 서버는 현재 오픈 베타로 제공된다.",
  "MarketScale", "https://www.marketscale.com/industries/marketing-tech/6sense-embeds-buyer-intelligence-into-claude-chatgpt-writer-and-agentforce-with-four-new-product-releases"),
 ("세이스믹-하이스팟 합병 완료, GTM 플랫폼 출범",
  "세일즈 인에이블먼트 기업 세이스믹이 하이스팟과의 합병을 마무리하고 350만 사용자 규모의 GTM 성과 플랫폼을 출범시켰다. 연간 1억 달러 이상을 AI 에이전트와 콘텐츠 거버넌스 연구개발에 투자할 계획이다.",
  "Seismic", "https://www.seismic.com/newsroom/press-releases/seismic-completes-merger-with-highspot/"),
 ("마테크 스택의 AI 에이전트, 보이지 않는 보안 구멍 될 수도",
  "여러 자율 AI 에이전트가 캠페인 실행을 위해 데이터를 주고받는 구조가 새로운 공격 표면을 만든다는 경고가 나왔다. 실시간 토큰화 프록시, 제로 데이터 보존 정책, 엄격한 권한 통제 등이 대응책으로 제시됐다.",
  "MarTech", "https://martech.org/the-terrifying-loophole-in-your-autonomous-tech-stack/"),
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
