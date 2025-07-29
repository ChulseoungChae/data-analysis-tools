#!/bin/bash

# Streamlit 리버스 프록시 플랫폼 실행 스크립트

echo "🌐 Streamlit 리버스 프록시 플랫폼 시작 중..."

# Python 가상환경 확인
if [ ! -d "venv" ]; then
    echo "📦 가상환경 생성 중..."
    python3 -m venv venv
fi

# 가상환경 활성화
echo "🔧 가상환경 활성화..."
source venv/bin/activate

# 의존성 설치
echo "📥 패키지 설치 중..."
pip install -r requirements.txt

# 데이터 디렉토리 생성
echo "📁 데이터 디렉토리 생성..."
sudo mkdir -p /mnt/data
sudo chmod 755 /mnt/data

# Streamlit 앱 실행 (성능 최적화 옵션 포함)
echo "🚀 Streamlit 앱 시작..."
echo "📍 웹 인터페이스: http://localhost:8501"
echo "📍 프록시 서버: http://localhost:8080 (자동 포트 선택)"
echo "📍 포트포워딩: 자동으로 활성 매핑에 적용"
echo ""
echo "종료하려면 Ctrl+C를 누르세요."

# 성능 최적화 옵션으로 실행
streamlit run app.py \
    --server.port 8501 \
    --server.address 0.0.0.0 \
    --server.maxUploadSize 200 \
    --server.maxMessageSize 200 \
    --server.enableCORS false \
    --server.enableXsrfProtection false \
    --browser.gatherUsageStats false 