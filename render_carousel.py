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
 ("Anthropic, SEC에 기밀 IPO 신청 — $965B 밸류·연매출 $470억",
  "앤트로픽이 증권거래위원회에 기밀 Form S-1 등록신청서를 제출했다. 최근 완료한 650억 달러 조달로 기업가치 9650억 달러에 달하며 연간매출은 470억 달러를 기록 중이다. 상장 시 기업가치 1조 달러 돌파가 유력하다.",
  "TechCrunch", "https://techcrunch.com/2026/06/01/anthropic-files-to-go-public/"),
 ("Microsoft MAI-Code-1-Flash 출시 — GitHub Copilot 탑재, Claude Haiku 초월",
  "마이크로소프트가 자체 개발 AI 코딩 모델 MAI-Code-1-Flash를 GitHub Copilot에 탑재했다. 5B 파라미터 규모로 SWE-Bench Pro에서 Claude Haiku 4.5를 16점 차로 앞섰으며 입력 토큰당 $0.75에 제공된다.",
  "CNBC", "https://www.cnbc.com/2026/06/02/microsoft-unveils-new-ai-models-lessen-reliance-on-openai-lower-costs.html"),
 ("트럼프, AI·사이버보안 행정명령 서명 — 출시 30일 전 자발적 모델 제출 요청",
  "트럼프 대통령이 6월 2일 'AI 혁신·보안 촉진' 행정명령에 서명했다. 기업들에 출시 30일 전 강력한 AI 모델을 정부에 자발적으로 제출해 테스트받도록 요청하고, AI 사이버보안 정보공유소(clearinghouse)를 신설한다.",
  "CNBC", "https://www.cnbc.com/2026/06/02/trump-executive-order-ai.html"),
 ("Anthropic, Claude 파트너 네트워크 서비스 트랙·파트너 허브 공개",
  "앤트로픽이 Claude 파트너 네트워크에 컨설팅 기업의 Claude 도입 깊이를 등급화하는 서비스 트랙과, 기업이 구현 파트너를 찾을 수 있는 파트너 허브를 추가했다. 파트너 가입 신청은 4만여 곳을 돌파했다.",
  "Yahoo Finance", "https://finance.yahoo.com/sectors/technology/articles/anthropic-launches-claude-partner-network-134019200.html"),
 ("Microsoft MAI-Thinking-1 추론 모델 공개 — 독자 AI 생태계 완성",
  "마이크로소프트가 Build 2026에서 추론 모델 MAI-Thinking-1과 코딩 모델 MAI-Code-1-Flash를 동시에 공개했다. OpenAI 의존도를 줄이고 자체 AI 모델 생태계를 구축하겠다는 의지를 천명했다.",
  "Neowin", "https://www.neowin.net/news/microsoft-unveils-mai-thinking-1-reasoning-and-mai-code-1-coding-models/"),
 ("구글·Microsoft, AI 코딩 시장서 Anthropic·OpenAI에 정면 도전",
  "클라우드 거인 구글과 마이크로소프트가 방대한 재원과 클라우드 사업을 앞세워 Anthropic Claude Code·OpenAI Codex에 맞서는 AI 코딩 툴 경쟁에 본격 뛰어들었다.",
  "CNBC", "https://www.cnbc.com/amp/2026/06/01/microsoft-and-google-take-on-anthropic-and-openai-in-ai-coding-models.html"),
 ("Anthropic, 구글·Broadcom과 차세대 컴퓨트 다중 기가와트 파트너십 체결",
  "앤트로픽이 구글, Broadcom과 함께 차세대 AI 인프라에 필요한 다중 기가와트 규모의 컴퓨팅 역량을 확보하는 파트너십을 발표했다. AI 학습·추론에 필요한 전력 및 칩 공급망을 강화한다.",
  "Anthropic", "https://www.anthropic.com/news/google-broadcom-partnership-compute"),
]

DESIGN = [
 ("Figma Config 2026 개막 예고 — 6월 23일 SF, AI 디자인 혁신 집약 행사",
  "피그마의 연례 개발자 컨퍼런스 Config 2026이 6월 23-25일 샌프란시스코 Moscone Center에서 개최된다. AI 기반 디자인 도구의 미래를 제시할 주요 발표가 예정돼 있으며 전 세계 워치파티도 운영된다.",
  "Figma", "https://config.figma.com/"),
 ("Figma Make, 프로덕션 코드베이스 직접 편집 베타 — 디자인·개발 핸드오프 혁신",
  "피그마가 5월 28일 디자인 화면을 벗어나지 않고 프로덕션 코드베이스를 직접 편집하는 Figma Make 기능을 베타로 공개했다. 직접 편집·주석·채팅·PR 생성이 가능하며 베타 기간 중 크레딧이 소비되지 않는다.",
  "Medium", "https://medium.com/@Rythmuxdesigner/figmas-2026-updates-quietly-redefine-design-dev-handoff-and-not-everyone-s-ready-98307f2ea2a8"),
 ("Figma Slides, 섹션 기능 출시 — 프레젠테이션 구조 관리·내비게이션 강화",
  "피그마 슬라이드에 섹션 기능이 추가됐다. 슬라이드 행에 이름을 붙이고 드래그로 재정렬하며 발표자·청중 화면에서 섹션 간 직접 이동이 가능해져 복잡한 프레젠테이션 구조화가 쉬워졌다.",
  "Figma", "https://www.figma.com/release-notes/"),
 ("4Creative 리브랜드 — 영국 아이코닉 TV 쇼에서 추출한 130개 로고로 새 아이덴티티",
  "영국 채널4 크리에이티브 스튜디오 4Creative가 《그레이트 브리티시 베이크오프》, 《피크 쇼》 등 아이코닉 TV 프로그램의 비주얼 요소를 잘라내 130개 로고로 구성한 새 브랜드 아이덴티티를 완성했다.",
  "It's Nice That", "https://www.itsnicethat.com/articles/4creative-rebrand-graphic-design-project-070526"),
 ("2026 로고 디자인 트렌드 — 키네틱·움직이는 아이덴티티 시스템의 부상",
  "Creative Bloq가 선정한 2026 로고 트렌드에서 키네틱 로고와 유연한 아이덴티티 시스템이 두드러진다. 정적 스탬프 대신 맥락·환경에 따라 반응하고 움직이는 브랜드 마크가 주류로 자리 잡고 있다.",
  "Creative Bloq", "https://www.creativebloq.com/design/logos-icons/these-logo-design-trends-will-define-2026"),
 ("Creative Boom: \"이상해야 브랜드가 살아남는다\" — AI 동질화 반하는 2026 전략",
  "Creative Boom이 2026 브랜딩의 역설을 조명했다. AI가 모든 브랜드를 비슷하게 만드는 시대에 '이상함(weird)'을 전략적으로 택한 브랜드만이 진정한 차별화를 이룰 수 있다고 분석한다.",
  "Creative Boom", "https://www.creativeboom.com/insight/how-being-weird-can-save-branding-in-2026/"),
 ("Design.md 2026 — AI 프로덕트, 무작위 비주얼에서 체계적 디자인 규율로 진화",
  "2026년 6월 Design.md 뉴스가 AI로 만들어진 제품들이 무작위 시각 결과물에서 문서화된 디자인 규율로 진화하는 신호를 포착했다. AI 에이전트가 디자인 시스템 원칙을 준수하는 사례가 빠르게 늘고 있다.",
  "Mean CEO Blog", "https://blog.mean.ceo/design-md-news-june-2026/"),
]

MARKETING = [
 ("Google Marketing Live 2026 — Gemini AI 광고 에이전트·Direct Offers 全채널 확대",
  "구글이 5월 20일 마케팅 라이브에서 Google Ads·Analytics·Merchant Center를 아우르는 통합 Gemini AI 에이전트를 공개했다. Direct Offers를 일반 배포하고 AI 쇼핑 광고와 대화형 맞춤 포맷도 선보였다.",
  "Google Blog", "https://blog.google/products/ads-commerce/google-marketing-live-2026-collection/"),
 ("구글, AI 검색 시대 광고 전면 혁신 — 대화형 쿼리에 즉시 맞춤화",
  "구글이 AI 검색 시대에 최적화한 차세대 광고 포맷을 출시했다. 사용자의 고유한 쿼리에 즉시 맞춤화되며 브랜드 발견과 구매 결정 사이의 간극을 좁히는 방식으로 설계됐다.",
  "Google Blog", "https://blog.google/products/ads-commerce/google-marketing-live-search-ads/"),
 ("Klaviyo, AI 마케팅 에이전트 출시 — Anthropic Claude로 이메일 자동 생성·최적화",
  "클라비요가 Anthropic Claude를 통합한 AI 마케팅 에이전트를 출시했다. 과거 고객 인터랙션을 분석해 구매 유도 문구를 예측하고, 이메일 텍스트 작성과 디지털 캠페인 개선 제안을 자동화한다.",
  "MarTech", "https://martech.org/klaviyo-introduces-marketing-agent-to-automate-campaigns/"),
 ("Attentive, Thread 2026서 에이전틱 AI 공개 — 멀티채널 참여도·구매 의도 자동 분석",
  "어텐티브가 연간 고객 행사 Thread 2026에서 채널별 메시지 전달 신호를 분석해 참여도와 구매 의도를 평가하는 에이전틱 AI를 공개했다. 복수 채널 캠페인 최적화를 실시간으로 자동화한다.",
  "Agile Brand Guide", "https://agilebrandguide.com/yesterdays-marketing-technology-ai-news-june-2-2026/"),
 ("ZoomInfo GTM.AI, MCP 기반 에이전트 호출 가능 데이터 레이어 출시",
  "줌인포가 6월 1일 AI 에이전트가 MCP와 공개 API를 통해 직접 호출하는 데이터 레이어를 출시했다. 독점 데이터를 거버넌스 적용 레이어로 노출해 에이전트가 실시간으로 접근할 수 있게 한다.",
  "B2the7", "https://www.b2the7.com/news-blog/marketing-trends-june-2026-ai-search-chatgpt-ads-meta"),
 ("「2026 에이전틱 AI 마케팅 현황」 — 마케터 80.6%, AI는 여전히 '어시스트 모드'",
  "타입페이스 AI 보고서에 따르면 마케터 80.6%가 AI 에이전트를 '어시스트 모드'(AI가 제안, 인간이 결정)로 활용하며, 37.9%만이 '승인 후 실행'으로 이행했다. AI가 전략 차별화 도구로 자리잡는 과정을 보여준다.",
  "Typeface AI", "https://www.typeface.ai/blog/state-of-agentic-ai-in-marketing-2026"),
 ("마테크, 브랜드 전략 대체하는 '컨트롤 플레인'으로 진화",
  "캠페인 실행 자동화에서 출발한 마케팅 기술 스택이 이제 브랜드 전략 자체를 대체하는 통합 컨트롤 플레인으로 역할이 확장됐다고 MarTech Series가 분석했다. AI가 마케팅 운영을 전략적 비즈니스 성과와 직결시킨다.",
  "MarTech Series", "https://martechseries.com/mts-insights/staff-writers/from-campaigns-to-control-planes-why-martech-is-replacing-brand-strategy/"),
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
