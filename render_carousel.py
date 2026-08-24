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
 ("OpenAI, 유럽 31개국에 ChatGPT 광고 확대",
  "OpenAI가 8월 24일부터 독일·프랑스·스페인 등 유럽 31개 시장에 ChatGPT 광고를 도입한다고 발표했다. 무료 및 Go 요금제 사용자에게만 노출되며, GDPR에 따라 광고주는 대화 기록에 접근할 수 없다. Plus·Pro·Enterprise 등 유료 요금제는 광고 없이 유지된다.",
  "OpenAI", "https://openai.com/index/chatgpt-ads-expands-across-europe/"),
 ("NVIDIA, AI 서버 가격 15% 이상 인상 통보",
  "블룸버그에 따르면 NVIDIA가 마이크로소프트·알파벳·오라클 등 주요 고객사에 Vera Rubin·Grace Blackwell 기반 AI 서버 가격을 15% 이상 인상한다고 통보했다. 메모리 칩 원가 상승이 주요 원인이며, 인상분은 내년 초 출하분부터 적용된다.",
  "Bloomberg", "https://www.bloomberg.com/news/articles/2026-08-22/nvidia-customers-notified-about-ai-related-price-hikes-above-15"),
 ("Anthropic, 기업 고객 데이터 보관 정책 수정",
  "Anthropic이 기업 고객 반발을 수용해 데이터 보관 정책을 변경한다. 30일 보관 원칙은 유지하되 자사 클라우드가 아닌 고객 자체 인프라에 데이터를 저장하는 옵션을 올가을 도입할 계획이며, Salesforce를 포함한 100여 개 고객사와 협의를 거쳤다.",
  "The Decoder", "https://the-decoder.com/anthropic-changes-data-retention-policy-after-enterprise-pushback/"),
 ("OpenAI, 기업 시장에서 Anthropic 추격",
  "TechCrunch가 입수한 데이터에 따르면 OpenAI가 기업용 AI 시장에서 Anthropic과의 점유율 격차를 좁히고 있다. 코딩·에이전트 분야에서 앞서가던 Anthropic에 맞서 OpenAI가 비즈니스 고객 확보에 속도를 내고 있다.",
  "TechCrunch", "https://techcrunch.com/2026/08/20/openai-is-gaining-on-anthropic-with-business-users-new-data-indicates/"),
 ("AI 반도체 스타트업 Etched, 한 달 만에 기업가치 두 배",
  "AI 추론 칩 스타트업 Etched가 Jane Street 주도로 7억 달러를 추가 유치하며 기업가치 210억 달러를 인정받았다. 지난해 12월 50억 달러였던 기업가치가 한 달 새 110억 달러 가까이 뛰었다.",
  "TechCrunch", "https://techcrunch.com/2026/08/18/etcheds-valuation-doubles-to-21b-in-a-month/"),
 ("Block, 오픈소스 AI 에이전트 워크스페이스 'Berd' 공개",
  "Jack Dorsey가 이끄는 Block이 아파치 2.0 라이선스로 AI 에이전트 워크스페이스 Berd를 오픈소스로 공개했다. 여러 모델과 에이전트 하네스를 넘나들며 작동하고 대화 기록을 로컬에 저장하는 것이 특징이다.",
  "VentureBeat", "https://venturebeat.com/orchestration/blocks-new-apache-2-0-agent-workspace-berd-works-across-models-and-harnesses-stores-conversation-history-locally"),
 ("음성 AI 스타트업 Wispr, 2800억 원 규모 시리즈B 유치",
  "받아쓰기 도구로 시작한 Wispr가 Menlo Ventures 주도로 2억8000만 달러를 유치하며 기업가치 20억 달러를 인정받았다. 회의록 작성과 소음 환경 인식률을 높인 자체 음성모델 Kanto를 앞세워 사업을 확장할 계획이다.",
  "TechCrunch", "https://techcrunch.com/2026/08/17/wispr-raises-280m-at-2b-valuation-as-it-looks-beyond-dictation/"),
]

DESIGN = [
 ("Studio Blackburn, Brompton 50주년 새 브랜드 아이덴티티 공개",
  "영국 디자인 스튜디오 Studio Blackburn이 접이식 자전거 브랜드 Brompton의 50주년을 기념해 새 아이덴티티를 선보였다. 기존 트립틱 로고는 유지하되 커스텀 서체 'Brompton Slab'과 브롬튼 블루 컬러, 접히는 동작에서 착안한 모션 시스템을 새로 도입했다.",
  "BP&O", "https://bpando.org/2026/08/20/studio-blackburns-new-identity-for-folding-bike-icon-brompton-cements-the-brands-place-in-british-engineering-heritage/"),
 ("Instagram 새 로고 두고 디자인 업계 의견 첨예하게 갈려",
  "Instagram의 리디자인을 맡은 스튜디오 Koto가 손글씨와 기하학적 형태를 결합한 새 로고를 공개했으나, 'r'자가 'z'처럼 보여 “Instagzam”으로 읽힌다는 비판이 제기됐다. 가독성 문제 지적과 전체 시스템 안에서 평가해야 한다는 반박이 맞서며 논쟁이 이어지고 있다.",
  "Creative Boom", "https://www.creativeboom.com/insight/creatives-are-deeply-divided-over-the-new-instagram-logo-and-i-think-that-points-to-a-broader-issue/"),
 ("Nielsen Norman Group, 제품·디자인 실무자 위한 AI 용어 가이드 발행",
  "Nielsen Norman Group이 토큰, 컨텍스트 윈도우, 에이전트, 프롬프트 인젝션 등 제품·디자인 업무에서 자주 등장하는 AI 용어를 쉬운 언어로 정리한 가이드를 게재했다. 디자이너와 UX 실무자가 AI 도구 관련 용어를 빠르게 이해하도록 돕는다.",
  "Nielsen Norman Group", "https://www.nngroup.com/articles/artificial-intelligence-glossary/"),
 ("NN/g 연구, AI 생성 이미지 신뢰도 스톡 사진과 대등",
  "Nielsen Norman Group이 77명을 대상으로 진행한 실험에서 출처를 밝히지 않을 경우 AI 생성 이미지가 신뢰도·전문성·진정성 평가에서 스톡 사진과 대등하거나 소폭 앞섰다. 다만 AI 생성 사실을 밝히면 인식이 부정적으로 바뀔 수 있어 개별 검토가 필요하다고 조언했다.",
  "Nielsen Norman Group", "https://www.nngroup.com/articles/ai-generated-images/"),
 ("Perky Bros, 테네시 리조트 Southall 위한 절제된 아이덴티티",
  "내슈빌 기반 스튜디오 Perky Bros가 농장·호텔·스파·미쉐린 레스토랑을 갖춘 325에이커 규모 테네시 리조트 Southall의 비주얼 아이덴티티를 완성했다. 다이아몬드 세 개로 이루어진 심볼과 커스텀 서체 'Grifo S', 지역 셰일석에서 착안한 질감으로 자연 경관과 서던 호스피탈리티를 부각했다.",
  "BP&O", "https://bpando.org/2026/08/18/perky-bros-southern-hospitality-tennessee-resort-southall/"),
 ("Figma 오토레이아웃에 'Around·Evenly' 간격 옵션 추가",
  "Figma가 오토레이아웃 기능에 기존 'Between' 방식과 함께 CSS 표준에 맞춘 'Around', 'Evenly' 간격 옵션을 추가했다. 캔버스에서 설정한 간격이 실제 CSS 코드로 변환될 때의 격차를 줄여 개발 핸드오프 정확도를 높인 업데이트다.",
  "Figma", "https://help.figma.com/hc/en-us/articles/31289464393751-Use-the-horizontal-and-vertical-flows-in-auto-layout"),
 ("It's Nice That, LA 그래픽 디자이너 James Junk의 오프라인 주말 조명",
  "연재 코너 'The Weekend With...'가 빈티지 감성으로 알려진 LA 기반 그래픽 디자이너 James Junk를 다뤘다. 비딩 워크숍 참여, 팝업 방문, 독서 등 화면에서 벗어난 활동으로 주말을 보내며 의도적으로 디지털과 거리를 두는 창작자의 재충전 방식을 조명했다.",
  "It's Nice That", "https://www.itsnicethat.com/features/the-weekend-with-james-junk-210826"),
]

MARKETING = [
 ("Omnicom, AI 플랫폼 개발 인력 수백 명 외주업체 Endava로 이전",
  "Adweek 단독 보도에 따르면 Omnicom이 자사 AI 플랫폼 'Omni'를 개발한 미국·영국·인도·말레이시아 소속 인력 최소 468명을 6~7월 사이 계약업체 Endava로 이전했다. 앞서 6월에는 같은 부서에서 미국 직원 약 50명을 별도로 감원했으며, 회사 측은 엔지니어링 역량 확대를 위한 다년 계약이라고 설명했다.",
  "Adweek", "https://www.adweek.com/agencies/exclusive-omnicom-offloads-hundreds-of-staffers-who-built-its-ai-platform-to-third-party-contractor/"),
 ("Neutrogena, Hayden Panettiere 사망 후 불매 여론에 공식 입장",
  "배우 Hayden Panettiere 사망 이후 과거 산후우울증 언급 뒤 광고 계약이 끊겼다는 발언이 재조명되며 불매 운동이 확산되자, Neutrogena는 그가 “10년 넘게 소중한 커뮤니티 일원이었다”며 “힘든 시기에 충분히 지지받지 못한다고 느끼게 한 점”을 인정하는 공식 성명을 냈다.",
  "Adweek", "https://www.adweek.com/brand-marketing/neutrogena-responds-to-hayden-panettiere-backlash-this-isnt-who-we-want-to-be/"),
 ("Coca-Cola, 프리미어리그 시즌 앞두고 Central Cee와 신규 캠페인",
  "Coca-Cola유로퍼시픽파트너스가 2026/27 프리미어리그 시즌을 맞아 영국 래퍼 Central Cee와 함께 'Taste Every Second' 캠페인을 시작했다. 오리지널 트랙 발매와 함께 리테일 매장 진열, 시식 행사, 워치파티 등 소셜·옥외광고 연계 활동이 진행된다.",
  "Talking Retail", "https://www.talkingretail.com/products-news/soft-drinks/coca-cola-kicks-off-premier-league-season-with-new-campaign-21-08-2026/"),
 ("Gap, 신작 공포영화 배우 기용해 가을 데님 캠페인 공개",
  "Gap이 영화 'Obsession'으로 주목받은 배우와 싱어송라이터 Malcolm Todd를 앞세운 가을 캠페인 'Denim on your own'을 선보였다. 뮤직비디오 형식의 광고로 타임스스퀘어와 선셋대로 등 옥외광고·디지털·리테일 채널을 통해 노출되며 Z세대 공략 전략을 이어간다.",
  "Marketing Dive", "https://www.marketingdive.com/news/gaps-fall-campaign-enlists-obsession-star-to-promote-denim/827375/"),
 ("YouTube, 커넥티드TV 시청 강화 위해 앱에 신규 기능 대거 추가",
  "YouTube가 크리에이터가 재생목록을 에피소드형 쇼로 전환할 수 있는 기능을 포함해 TV 앱을 대대적으로 개편했다. 전용 쇼·팟캐스트 탭과 4자리 잠금코드 등 어린이 콘텐츠 보호자 통제 기능도 함께 도입했다.",
  "Marketing Dive", "https://www.marketingdive.com/news/sociable-youtube-rolls-out-improvements-for-connected-tv/828408/"),
 ("Chipotle, 16세 크리에이터 Salish Matter와 손잡고 알파세대 공략",
  "Chipotle가 16세 유튜브·틱톡 크리에이터 Salish Matter와 협업해 그가 즐겨 주문하는 메뉴로 구성한 한정판 '키즈밀'을 출시했다. 부모 응답자의 91%가 알파세대 자녀가 브랜드 선호에 영향을 준다고 답한 조사를 바탕으로 한 전략이며, 인스타그램 이벤트로 LA 여행 기회도 제공한다.",
  "Marketing Dive", "https://www.marketingdive.com/news/chipotle-broadens-gen-alpha-marketing-strategy-with-salish-matter-collab/828178/"),
 ("IAB, 광고 내 AI 사용 공개 기준 담은 프레임워크 2.0 발표",
  "미국 인터랙티브광고협회(IAB)가 유럽·아시아·뉴욕·캘리포니아 등지의 새 규제 흐름을 반영한 'AI 투명성 및 공개 프레임워크' 2번째 버전을 공개했다. 과도한 AI 라벨링이 오히려 '라벨 피로도'를 유발할 수 있다고 짚었으며, 광고 임원의 83%가 이미 크리에이티브 과정에 AI를 도입한 것으로 나타났다.",
  "Marketing Dive", "https://www.marketingdive.com/news/iab-revisits-ai-disclosure-in-ads-as-legal-requirements-multiply/828182/"),
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
