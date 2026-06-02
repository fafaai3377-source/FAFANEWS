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
DATE_ISO = datetime.date(2026, 6, 2)
DATE = "2026년 6월 2일 (화)"

AI = [
 ("엔비디아 COMPUTEX 2026 — 젠슨 황, PC 재발명·로봇 선언",
  "6월 1일 젠슨 황 CEO가 컴퓨텍스 키노트에서 RTX Spark 노트북·데스크톱, Vera CPU(앤트로픽·OpenAI 납품 중), 인간형 로봇 레퍼런스 설계 Isaac GR00T를 발표했다. '컴퓨트가 곧 매출'이라는 메시지로 AI 인프라 투자를 정당화했다.",
  "Fortune", "https://fortune.com/2026/06/01/jensen-huang-nvidia-pc-reinvention-ai-chips/"),
 ("구글 Gemini 3.5 Flash, 정식 버전(GA) 전환",
  "구글의 경량 고속 AI 모델 Gemini 3.5 Flash가 정식 출시로 전환됐다. 동급 모델 대비 4배 빠른 속도로 프런티어급 지능을 제공하며 컨텍스트 창 100만 토큰, 가격 $1.50/$9(입력/출력)를 유지한다.",
  "LLM Stats", "https://llm-stats.com/llm-updates"),
 ("OpenAI GPT-5.5 Instant, ChatGPT 기본 모델 교체",
  "OpenAI가 GPT-5.5 Instant를 ChatGPT의 새 기본 모델로 지정했다. 이전 기본 모델인 GPT-5.3 Instant를 대체하며 더 빠른 응답 속도와 향상된 자연어 이해를 제공한다.",
  "TechCrunch", "https://techcrunch.com/2026/05/05/openai-releases-gpt-5-5-instant-a-new-default-model-for-chatgpt/"),
 ("법률 AI 스타트업 Legora, $550M 시리즈 D — 밸류 $5.55B",
  "스웨덴 법률 AI 스타트업 Legora가 Accel 주도로 55억 달러 밸류에이션에 5억5000만 달러를 조달했다. 뉴욕에 이어 휴스턴·시카고로 미국 사무소를 확장하며 법률 전문가 8만 명 이상이 사용 중이다.",
  "Bloomberg", "https://www.bloomberg.com/news/articles/2026-03-10/legal-ai-startup-legora-raises-550-million-for-us-expansion"),
 ("Runware, $50M 시리즈 A — AI 이미지·영상 단일 API",
  "Runware가 Dawn Capital 주도로 5000만 달러 시리즈 A를 완료했다. 이미지·영상 생성 AI 모델 200만+ 개를 단일 Sonic Inference Engine API로 통합해 개발자 20만 명, 3억 명 이상 최종 사용자에게 서비스한다.",
  "TechCrunch", "https://techcrunch.com/2025/12/11/runware-raises-50m-series-a-from-dawn-capital-comcast-ventures-to-become-the-api-for-all-ai/"),
 ("Alteryx, Inspire 2026서 Agent Studio·MCP 서버 공개",
  "Alteryx가 Inspire 2026 컨퍼런스에서 기존 데이터 워크플로를 자율 에이전트로 변환하는 Agent Studio와 MCP 서버를 선보였다. 중앙 IT 팀 없이 비즈니스 애널리스트가 직접 도메인 AI 에이전트를 구축·운영할 수 있다.",
  "Crescendo AI", "https://www.crescendo.ai/news/latest-ai-news-and-updates"),
 ("프롬프트 위치가 정답을 바꾼다 — 50개 모델 3년 연구",
  "50개 프런티어 AI 모델을 3년간 분석한 연구에서 프롬프트 내용보다 정보의 위치 배치가 모델 응답에 더 크게 영향을 미친다는 결과가 나왔다. AI 시스템 설계와 프롬프트 엔지니어링 전략에 시사점을 준다.",
  "ScienceDaily", "https://www.sciencedaily.com/news/computers_math/artificial_intelligence/"),
]

DESIGN = [
 ("피그마 AI 에이전트, 디자인 캔버스 직접 내장 베타 출시",
  "5월 20일 피그마가 캔버스에 직접 내장된 AI 에이전트 한정 베타를 공개했다. UI 요소를 클릭하면 자연어로 디자인을 조정할 수 있고 기존 디자인 시스템을 자동으로 준수한다.",
  "Fast Company", "https://www.fastcompany.com/91545179/figma-ai-agent-tool"),
 ("피그마, 이미지 정밀 편집 AI 3종 도구 공개",
  "피그마가 개체 지우기·개체 분리·이미지 확장 3가지 AI 이미지 편집 도구를 새 전용 툴바와 함께 출시했다. 복잡한 이미지 합성 작업을 비전문가도 직관적으로 수행할 수 있게 됐다.",
  "Figma Blog", "https://www.figma.com/blog/introducing-three-new-tools-for-precise-image-editing-in-figma/"),
 ("라코스테, Commission Studio와 헤리티지 아이덴티티 리프레시",
  "라코스테가 런던 Commission Studio와 협업해 세리프 워드마크와 붉은 혀가 강조된 새 악어 로고, 클레이·파린 헤리티지 컬러 팔레트를 도입했다. 창업자 르네 라코스테 아카이브에서 영감을 얻은 리프레시다.",
  "Creative Review", "https://www.creativereview.co.uk/lacoste-new-visual-identity-commission-studio/"),
 ("BBH, 44년 만의 첫 비주얼 아이덴티티 개편",
  "광고 에이전시 BBH가 창립 44년 만에 첫 리브랜드를 단행했다. London Studio Drama와 협업해 전용 서체 3종·ZAG 글리프·단편 영화·모션 그래픽으로 'AI 획일화'에 반기를 든 창업자 정신을 담았다.",
  "It's Nice That", "https://www.itsnicethat.com/articles/bbh-studio-drama-rebrand-graphic-design-project-260226"),
 ("2026 로고 트렌드 — 키네틱·적응형 비주얼 시스템의 부상",
  "2026년 로고 디자인은 정적 스탬프에서 벗어나 플랫폼·맥락에 따라 변형·움직이는 모션 우선 키네틱 시스템으로 진화하고 있다. 브랜드 아이덴티티가 살아 있는 유기체처럼 반응하는 구조로 재설계되는 추세다.",
  "Creative Bloq", "https://www.creativebloq.com/design/logos-icons/these-logo-design-trends-will-define-2026"),
 ("UX/UI 트렌드 2026 — 차분한 인터페이스·투명한 AI 설계",
  "2026년 UX/UI는 시각적 과잉을 걷어내고 의도적 단순함을 추구하는 방향으로 정착했다. AI 기능을 투명하게 표시하고 접근성을 기본값으로 삼는 '차분한 인터페이스' 설계가 주류 기준이 됐다.",
  "Envato Elements", "https://elements.envato.com/learn/ux-ui-design-trends"),
 ("Porto Rocha — W호텔·Google Gemini·Sundance 스튜디오 조명",
  "2019년 뉴욕 설립 Porto Rocha가 W호텔·구글 Gemini·나이키 런·선댄스 영화제 등 대형 글로벌 리브랜드를 연달아 성공시켜 디자인계 최고 주목 스튜디오로 꼽혔다.",
  "Creative Boom", "https://www.creativeboom.com/inspiration/15-studios-creatives-are-excited-about-right-now-beyond-the-obvious/"),
]

MARKETING = [
 ("구글 마케팅 라이브 2026: 'Ask Advisor' — Gemini 기반 통합 광고 인터페이스",
  "구글이 Google Ads·Analytics·Merchant Center를 하나로 통합한 Gemini 기반 대화형 인터페이스 'Ask Advisor'를 발표했다. 작업 간 공유 메모리를 갖춰 단일 AI 에이전트로 구글 광고 전체 스택을 관리할 수 있다.",
  "LBB Online", "https://lbbonline.com/news/Google-Marketing-Live-2026"),
 ("메타, 2026년 글로벌 광고 1위 — 구글 20년 만에 첫 역전",
  "메타가 2026년 글로벌 디지털 광고 수익에서 구글을 처음으로 앞지를 것으로 예상된다. 메타 $2,434억 대 구글 $2,395억으로, 약 20년 만의 왕좌 교체다.",
  "B2the7", "https://www.b2the7.com/news-blog/marketing-trends-june-2026-ai-search-chatgpt-ads-meta"),
 ("TikTok 광고 MCP 서버 출시 — AI 에이전트가 캠페인 전체 자동 운영",
  "5월 TikTok World에서 TikTok이 광고 플랫폼에 MCP 서버를 출시했다. 마케터의 AI 에이전트가 입찰·예산·타겟팅·소재 생성까지 수동 개입 없이 TikTok 캠페인 전 과정을 자동으로 운영한다.",
  "Digiday", "https://digiday.com/marketing/tiktok-launches-mcp-server-to-let-ai-agents-run-campaigns/"),
 ("Meta MCP 서버 출시 — Claude·ChatGPT로 광고 계정 직접 관리",
  "메타가 Claude·ChatGPT 등 외부 AI를 통해 광고 계정을 직접 관리할 수 있는 MCP 서버를 출시했다. 광고주는 메타 광고 관리자 대시보드를 열지 않고 AI와의 대화만으로 캠페인을 실행·최적화할 수 있다.",
  "Adweek", "https://www.adweek.com/media/tiktok-builds-for-the-ai-future-welcoming-third-party-agents-for-ads/"),
 ("Google Ads MCP 서버 오픈소스 공개 — AI-광고 플랫폼 직접 연결",
  "구글이 Google Ads API와 AI 모델이 커스텀 코드 없이 직접 상호작용할 수 있는 오픈소스 Google Ads MCP 서버를 공개했다. 주요 4대 광고 플랫폼 모두 MCP 기반 AI 에이전트 인프라 구축이 완료됐다.",
  "B2the7", "https://www.b2the7.com/news-blog/marketing-trends-june-2026-ai-search-chatgpt-ads-meta"),
 ("2026 소셜 미디어 — 퍼스널 브랜드가 기업 페이지를 압도",
  "2026년 소셜 미디어에서는 창업자·직원 등 실제 인물 콘텐츠가 기업 로고 계정보다 신뢰·도달·매출 모두에서 앞서고 있다. AI는 초안 작성을 보조하되 진정성은 사람에게서 나온다는 공식이 굳어졌다.",
  "Social Media Marketing Trends", "https://blog.mean.ceo/social-media-marketing-trends-june-2026/"),
 ("2026 최고의 마케팅 캠페인 — 진정성·팬덤 기반 참여가 핵심",
  "2026년 최고의 마케팅 캠페인은 문화적 연관성과 팬덤 기반 몰입 경험을 중심으로 설계됐다. 브랜드 스토리는 아이러니를 버리고 진정한 감정 연결을 추구하는 방향으로 확연히 선회했다.",
  "Brand Vision", "https://www.brandvm.com/post/best-marketing-campaigns-2026"),
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
