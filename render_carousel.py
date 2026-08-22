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
 ("슬랙 코드, AI 코딩 에이전트를 팀 채널로",
  "슬랙이 클로드 코드·데빈·깃허브 코파일럿·버셀 에이전트를 전용 프로젝트 채널에 내장한 '슬랙 코드'를 출시했다. 팀원 전체가 실시간으로 코드 변경을 지켜보고 검토·승인할 수 있다.",
  "Slack Blog", "https://slack.com/blog/news/slack-code-channels-for-agents"),
 ("앤트로픽, 10월 IPO 목표로 밸류 2조 달러 추진",
  "앤트로픽이 10월 상장을 목표로 IPO 절차에 속도를 내고 있으며 목표 기업가치는 2조 달러로 스페이스X의 최대 기록을 넘어설 전망이다. 모건스탠리·골드만삭스·JP모건이 대표 주관사를 맡았다.",
  "Dataconomy", "https://dataconomy.com/2026/08/21/anthropic-accelerates-ipo-plans-targeting-2-trillion/"),
 ("엔비디아, 풀사이드에 60억 달러 라이선스·10억 달러 투자",
  "엔비디아가 AI 모델 개발 스타트업 풀사이드의 '모델 팩토리' 기술에 60억 달러 규모 비독점 라이선스 계약을 맺고, 120억 달러 밸류에서 10억 달러를 추가 투자했다. 인수가 아닌 독립 운영 유지가 조건이다.",
  "PYMNTS", "https://www.pymnts.com/news/artificial-intelligence/2026/nvidia-pays-6-billion-to-license-poolside-ai-model-development-software/"),
 ("구글, AI발 트래픽 감소에 '선호 출처' 버튼으로 대응",
  "구글이 AI 검색으로 줄어드는 클릭을 만회하도록 독자가 특정 매체를 '선호 출처'로 지정할 수 있는 버튼을 공개했다. 검색·디스커버·구글 뉴스 전반에서 해당 매체 노출이 늘어난다.",
  "TechCrunch", "https://techcrunch.com/2026/08/20/google-gives-publishers-a-new-way-to-fight-ai-driven-traffic-losses/"),
 ("MS, 통합 코파일럿 앱 전 세계 롤아웃 시작",
  "마이크로소프트가 코파일럿과 마이크로소프트 365 코파일럿을 하나의 앱으로 합치는 통합 롤아웃을 8월 중순 시작했다. 모바일이 먼저 적용되고 윈도우·맥OS는 9월 중순부터 순차 적용된다.",
  "Windows Central", "https://www.windowscentral.com/artificial-intelligence/microsoft-copilot/microsoft-begins-unified-copilot-app-rollout-reveals-major-plan-to-merge-copilot-and-microsoft-365-copilot-across-all-platforms-along-with-updated-branding"),
 ("xAI, 그록 봇 정식 확대 — 상시 AI 동료",
  "xAI가 앱·받은편지함을 넘나들며 업무를 대신 처리하는 AI 동료 '그록 봇'을 베타에서 정식 확대했다. 슈퍼그록 플러스·헤비와 커서 프로플러스·울트라·팀 요금제에서 이용할 수 있다.",
  "VentureBeat", "https://venturebeat.com/orchestration/spacexais-grok-bot-turns-agents-into-persistent-digital-coworkers-that-can-operate-your-apps-for-120-per-month"),
 ("앤트로픽, 클로드로 단백질 결합체 설계 — 15개 표적 중 14개 성공",
  "앤트로픽이 클로드 모델로 자율 단백질 설계 캠페인을 진행해 15개 표적 중 14개에서 유효 결합체를 얻었다고 밝혔다. 업계 평균의 두 배가 넘는 최대 35.1%의 성공률을 기록했다.",
  "Anthropic", "https://www.anthropic.com/research/Claude-accelerates-protein-design"),
]

DESIGN = [
 ("인스타그램, 10년 만에 워드마크·서체 전면 개편",
  "인스타그램이 2016년 이후 처음으로 워드마크를 새로 그리고 손글씨체 'Pen', 모노스페이스체 'Mono' 등 서체 3종을 추가했다. 상징인 그러데이션은 유지하되 한층 절제된 톤으로 적용된다.",
  "Creative Boom", "https://www.creativeboom.com/news/instagram-reveals-its-first-brand-refresh-in-10-years-with-a-new-wordmark-and-three-typefaces/"),
 ("피그마, '중첩 폴더' 기능으로 파일 관리 개편",
  "피그마가 폴더 안에 폴더를 최대 10단계까지 만들 수 있는 '중첩 폴더'를 출시했다. 프로젝트 명칭이 폴더로 바뀌고 접근 권한도 상위 폴더에서 상속되도록 단순화됐다.",
  "Figma Blog", "https://www.figma.com/blog/code-craft-and-the-making-of-nested-folders/"),
 ("코카콜라, 전 세계 200여 개국 비주얼 아이덴티티 통일",
  "코카콜라가 2021년 이후 최대 규모의 브랜드 일관성 프로젝트로 새 글로벌 비주얼 아이덴티티를 공개했다. 캔의 워드마크가 다시 세로형으로 바뀌고 제로슈거는 검정 리본과 세리프체로 차별화된다.",
  "PRINT Magazine", "https://www.printmag.com/branding-identity-design/coca-cola-unveils-a-new-global-brand-identity/"),
 ("D&AD '지름길의 대가' 리포트 — AI보다 중요한 건 인간의 판단력",
  "D&AD가 크리에이티브 리더 197명 인터뷰와 수상작 1만여 건 분석을 바탕으로 낸 보고서에서, AI를 지름길로만 쓰면 평범한 결과물만 남는다고 지적했다. 신입 일자리 감소로 판단력을 키울 기회 자체가 줄고 있다는 경고도 담겼다.",
  "Creative Boom", "https://www.creativeboom.com/news/dad-has-a-stark-message-for-creatives-the-industry-is-eating-itself-from-within/"),
 ("스튜디오 다이얼, 코드를 소재 삼아 메뉴판부터 도시계획까지",
  "런던의 스튜디오 다이얼이 아프간 전통 직물 문양을 리서치해 만든 생성형 패턴 도구와 칵테일을 젓는 동작에서 뽑아낸 메뉴 디자인 시스템을 선보였다. 코드를 종이·잉크 같은 소재로 다루는 접근이 특징이다.",
  "It's Nice That", "https://www.itsnicethat.com/articles/studio-dial-graphic-design-discover-110826"),
 ("지프, 30여 년 만의 첫 대규모 브랜드 리프레시",
  "땅콩버터 브랜드 지프가 30여 년 만에 처음으로 대대적인 브랜드 개편을 단행했다. 상징인 삼색 밴드를 되살리고 글자의 그림자 효과를 없애 더 깔끔하고 선명한 인상을 준다.",
  "PR Newswire", "https://www.prnewswire.com/news-releases/jif-unveils-first-major-brand-refresh-in-more-than-30-years-as-peanut-butter-moves-beyond-the-pbj-302846092.html"),
 ("NIQ 디자인 임팩트 어워드, 패키지 리뉴얼 8건 선정",
  "닐슨아이큐가 9회째를 맞은 디자인 임팩트 어워드 수상작을 발표했다. 리퀴드IV·호스티스 등 8개 브랜드가 쇼핑 경험과 실적 개선을 동시에 이룬 패키지 리뉴얼로 선정됐다.",
  "NielsenIQ", "https://nielseniq.com/global/en/news-center/2026/packaging-redesigns-see-4-average-volume-lift-niq-finds/"),
]

MARKETING = [
 ("팔로워 수보다 '핏'이 중요 — 데이터가 말하는 크리에이터 마케팅",
  "최신 데이터에 따르면 팔로워 수의 성과 예측력은 사실상 거의 없으며, 니치 팔로워 5만 명을 가진 크리에이터가 일반 팔로워 100만 명보다 전환율이 높다. 마케터의 팔로워 수 중시 응답률도 40%에서 8%로 급감했다.",
  "Marketing Dive", "https://www.marketingdive.com/news/creator-fit-beats-follower-count-for-brands-heres-what-the-numbers-say/828421/"),
 ("앤테일러, 서브스택 앞세운 10년 만의 통합 캠페인",
  "앤테일러가 다면적인 현대 워킹우먼을 겨냥해 거의 10년 만에 첫 대형 통합 마케팅 캠페인을 시작했다. 서브스택 콘텐츠와 뉴욕 패션위크 팝업 행사를 9월 8일 함께 선보인다.",
  "Marketing Dive", "https://www.marketingdive.com/news/ann-taylor-stages-a-brand-comeback-with-substack-fall-fashion-blitz/828397/"),
 ("크록스, 새 마스코트 '나일스'로 캐릭터 스토리텔링 시동",
  "크록스가 성격과 결점까지 지닌 시트콤 캐릭터 콘셉트의 공식 마스코트 '나일스'를 공개했다. 단발성 캠페인을 넘어 장기적으로 소비자와의 정서적 유대를 쌓겠다는 전략이다.",
  "Marketing Dive", "https://www.marketingdive.com/news/crocs-hatches-new-mascot-niles-to-enrich-brand-storytelling/827895/"),
 ("K18, 뉴욕 거리에 3m 높이 '털뭉치'를 굴리다",
  "헤어케어 브랜드 K18이 탈모 방지 세럼 출시를 알리기 위해 사람 머리카락으로 만든 3m 높이의 공을 뉴욕 도심 곳곳에서 굴리는 스턴트를 벌였다. 설명 없는 이미지로 먼저 화제를 모은 뒤 제품을 공개하는 전략을 택했다.",
  "Creative Boom", "https://www.creativeboom.com/news/a-10ft-ball-of-real-human-hair-is-rolling-through-new-york-and-its-all-part-of-the-plan/"),
 ("WPP, 지주사 체제 버리고 '엘리베이트28' 단행",
  "WPP가 지주회사 구조를 버리고 단일 회사 체제로 전환하는 다년 계획 '엘리베이트28'을 발표했다. 4개 사업부·4개 권역으로 재편하고 5억 파운드 비용 절감을 목표로 한다.",
  "Marketing Dive", "https://www.marketingdive.com/news/wpp-abandons-holding-company-model-with-major-strategic-overhaul/813201/"),
 ("구글, 정치 이메일에 스팸 필터 우회 '검증 발신자' 통로 마련",
  "구글이 9월 8일부터 정치 단체가 신원을 검증하고 스팸 신고율을 0.3% 미만으로 유지하면 지메일 스팸 필터를 우회할 수 있는 '검증 발신자' 프로그램을 시작한다고 밝혔다. 발신자 인증과 수신자 피드백을 결합해 받은편지함 우선순위를 정하는 방식이다.",
  "MarTech", "https://martech.org/google-gives-political-email-a-lane-around-spam-filters/"),
 ("치프미디어, AMZ 어드바이저스·리치소셜 인수 — 틱톡숍 역량 확장",
  "치프미디어가 아마존·틱톡숍 등 커머스 플랫폼 전반의 유료 미디어·마켓플레이스 전략을 강화하기 위해 AMZ 어드바이저스와 소셜 커머스 대행사 리치소셜 지분을 인수했다고 밝혔다.",
  "Yahoo Finance", "https://finance.yahoo.com/media-advertising/articles/chief-media-acquires-amz-advisers-175900208.html"),
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
