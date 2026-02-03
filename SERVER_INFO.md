# 물타기 봇 - 서버 & Git 접속 정보

---

## 웹앱 접속
```
http://fundauto.cafe24.com
```

---

## SSH 서버 접속

### Windows (PowerShell / CMD)
```bash
ssh root@fundauto.cafe24.com
```
비밀번호 입력 후 접속

### 서버 파일 위치
```
/root/trading_bot/
├── server.py           # 메인 서버 코드
├── config.py           # API 키, 설정값
├── static/index.html   # 웹 UI
├── averaging_state.json    # 물타기 상태
├── watchlist.json      # 워치리스트
└── trades.json         # 매매 기록
```

---

## 서버 관리 명령어

### 서버 상태 확인
```bash
ssh root@fundauto.cafe24.com "ps aux | grep python"
```

### 서버 로그 확인
```bash
ssh root@fundauto.cafe24.com "tail -50 /root/trading_bot/server.log"
```

### 서버 재시작
```bash
ssh root@fundauto.cafe24.com "pkill -f 'python3.*server.py'; sleep 2; cd /root/trading_bot && nohup python3 server.py > server.log 2>&1 &"
```

### 서버 중지
```bash
ssh root@fundauto.cafe24.com "pkill -f 'python3.*server.py'"
```

---

## 파일 업로드/다운로드 (SCP)

### 서버에서 다운로드
```bash
scp root@fundauto.cafe24.com:/root/trading_bot/server.py ./
```

### 서버로 업로드
```bash
scp ./server.py root@fundauto.cafe24.com:/root/trading_bot/
```

---

## Git 저장소

### GitHub 주소
```
https://github.com/magic3899-star/short-grid
```

### Git Clone (다른 컴퓨터에서)
```bash
git clone https://github.com/magic3899-star/short-grid.git
cd short-grid
```

### Git Pull (최신 코드 받기)
```bash
git pull
```

### Git Push (변경사항 업로드)
```bash
git add -A
git commit -m "변경 내용"
git push
```

---

## 개발 워크플로우

### 서버 코드 수정 순서
1. 서버에서 파일 다운로드
   ```bash
   mkdir trading_bot
   scp root@fundauto.cafe24.com:/root/trading_bot/server.py trading_bot/
   ```

2. 로컬에서 수정 (VSCode 등)

3. 서버에 업로드
   ```bash
   scp trading_bot/server.py root@fundauto.cafe24.com:/root/trading_bot/
   ```

4. 서버 재시작
   ```bash
   ssh root@fundauto.cafe24.com "pkill -f 'python3.*server.py'; sleep 2; cd /root/trading_bot && nohup python3 server.py > server.log 2>&1 &"
   ```

5. 로컬 파일 삭제
   ```bash
   rm -rf trading_bot/
   ```

6. Git 백업
   ```bash
   mkdir trading_bot
   scp root@fundauto.cafe24.com:/root/trading_bot/server.py trading_bot/
   scp root@fundauto.cafe24.com:/root/trading_bot/config.py trading_bot/
   scp root@fundauto.cafe24.com:/root/trading_bot/static/index.html trading_bot/
   git add -A
   git commit -m "변경 내용"
   git push
   rm -rf trading_bot/
   ```

---

## 주의사항

- **로컬에서 서버 실행 금지** - 항상 cafe24 서버에서만 실행
- **서버 파일이 기준** - 로컬은 수정/백업용
- **API 키는 config.py에** - Git에 올라가지 않도록 주의
