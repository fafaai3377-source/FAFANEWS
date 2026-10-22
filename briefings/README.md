# 📬 FAFA NEWS 브리핑 보관함

매일 생성되는 `YYMMDD_FAFA NEWS.pdf`가 이 폴더에 자동으로 쌓입니다.

- 생성 주체: GitHub Actions (`.github/workflows/daily.yml`)
- 갱신 시점: 매일 아침 예약 세션이 뉴스 데이터를 갱신·push → Actions가 재렌더링 후 이 폴더에 PDF 커밋
- 파일명 형식: `YYMMDD_FAFA NEWS.pdf` (예: `261023_FAFA NEWS.pdf`)

## 💻 데스크탑 자동 다운로드 설정 (최초 1회)

이 저장소를 데스크탑의 새 폴더로 클론하면, `git pull` 할 때마다 최신 PDF가 로컬 폴더에 내려받아집니다.

```bash
git clone https://github.com/fafaai3377-source/FAFANEWS.git ~/Desktop/FAFA-NEWS
```

- **GitHub Desktop** 사용 시: 위 저장소를 추가해두고 Fetch 하면 동기화
- **완전 자동 pull** 원하면 데스크탑 스케줄러에 매일 아침 pull 등록:
  - mac: `crontab -e` → `50 9 * * * cd ~/Desktop/FAFA-NEWS && git pull`
  - windows: 작업 스케줄러 → 매일 09:50 → `git -C %USERPROFILE%\Desktop\FAFA-NEWS pull`

그러면 `~/Desktop/FAFA-NEWS/briefings/` 폴더에 매일 PDF가 자동으로 생깁니다.
