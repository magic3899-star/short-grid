# 서버 배포 정보

## SSH 연결 정보
- 호스트: fundauto.cafe24.com
- 사용자: root
- 비밀번호: (직접 입력)

## 파일 업로드 방법

### 방법 1: SCP 명령어
```bash
scp 로컬파일경로 root@fundauto.cafe24.com:/root/trading_bot/
```

### 방법 2: 폴더 전체 업로드
```bash
scp -r 로컬폴더/ root@fundauto.cafe24.com:/root/trading_bot/
```

## 서버 폴더 생성 (먼저 실행 필요)
```bash
ssh root@fundauto.cafe24.com "mkdir -p /root/trading_bot"
```

## 서버 접속
```bash
ssh root@fundauto.cafe24.com
```

## 서버에서 실행
```bash
cd /root/trading_bot
python -m http.server 8080
# 또는 백그라운드 실행
nohup python -m http.server 8080 > server.log 2>&1 &
```

## 접속 URL
- http://fundauto.cafe24.com:8080/index.html
