import streamlit as st
import json
import requests
import socket
import time
import threading
from datetime import datetime, timedelta
from pathlib import Path
import shutil
import os
import hashlib
import base64
from proxy_server import ProxyServer
from port_forwarder import PortForwarder

# Streamlit 앱 설정
st.set_page_config(
    page_title="리버스 프록시 포트포워딩 플랫폼",
    page_icon="🔗",
    layout="wide",
    initial_sidebar_state="expanded"
)

# CSS로 여백 조정
st.markdown("""
<style>
/* 버튼 위아래 여백 줄이기 */
.stButton > button {
    margin-top: 0.3rem !important;
    margin-bottom: 0.3rem !important;
}

/* info 박스 아래 여백 줄이기 */
.stAlert {
    margin-bottom: 0.3rem !important;
}

/* 구분선 위아래 여백 줄이기 */
hr {
    margin-top: 0.3rem !important;
    margin-bottom: 0.3rem !important;
}

/* 제목 위아래 여백 줄이기 */
h1, h2, h3, h4, h5, h6 {
    margin-top: 0.5rem !important;
    margin-bottom: 0.5rem !important;
}

/* 컨테이너 여백 줄이기 */
.main .block-container {
    padding-top: 1rem !important;
    padding-bottom: 1rem !important;
}

/* 사이드바 여백 줄이기 */
.sidebar .sidebar-content {
    padding-top: 1rem !important;
}
</style>
""", unsafe_allow_html=True)

# 데이터 디렉토리 설정
DATA_DIR = Path("/mnt/data")
MAPPINGS_FILE = DATA_DIR / "port_mappings.json"
SERVERS_FILE = DATA_DIR / "servers.json"
USERS_FILE = DATA_DIR / "users.json"  # 사용자 계정 정보

# 백업 디렉토리 설정
BACKUP_DIR = Path.home() / "proxy_backup"
BACKUP_RETENTION_DAYS = 7  # 백업 보관 기간 (일)

# 백업 디렉토리 생성
BACKUP_DIR.mkdir(exist_ok=True)

def initialize_data_files():
    """데이터 파일 초기화"""
    if not MAPPINGS_FILE.exists():
        MAPPINGS_FILE.write_text(json.dumps([], indent=2))
    if not SERVERS_FILE.exists():
        SERVERS_FILE.write_text(json.dumps([], indent=2))
    if not USERS_FILE.exists():
        # 기본 관리자 계정 생성
        default_users = [
            {
                "id": "1",
                "username": "admin",
                "password": "admin123!",  # 실제 환경에서는 해시된 비밀번호 사용
                "role": "admin",
                "created_at": datetime.now().isoformat(),
                "last_login": None
            }
        ]
        USERS_FILE.write_text(json.dumps(default_users, indent=2))

def create_backup():
    """현재 데이터 파일들을 백업"""
    today = datetime.now().strftime("%Y-%m-%d")
    
    # 백업 파일명 생성
    mappings_backup = BACKUP_DIR / f"port_mappings_{today}.json"
    servers_backup = BACKUP_DIR / f"servers_{today}.json"
    users_backup = BACKUP_DIR / f"users_{today}.json"
    
    try:
        # 포트 매핑 백업
        if MAPPINGS_FILE.exists():
            shutil.copy2(MAPPINGS_FILE, mappings_backup)
        
        # 서버 정보 백업
        if SERVERS_FILE.exists():
            shutil.copy2(SERVERS_FILE, servers_backup)
        
        # 사용자 계정 백업
        if USERS_FILE.exists():
            shutil.copy2(USERS_FILE, users_backup)
        
        return True, f"백업 완료: {today}"
    except Exception as e:
        return False, f"백업 실패: {str(e)}"

def cleanup_old_backups():
    """오래된 백업 파일 정리"""
    cutoff_date = datetime.now() - timedelta(days=BACKUP_RETENTION_DAYS)
    
    try:
        for backup_file in BACKUP_DIR.glob("*.json"):
            # 파일 수정 시간 확인
            file_mtime = datetime.fromtimestamp(backup_file.stat().st_mtime)
            if file_mtime < cutoff_date:
                backup_file.unlink()
                print(f"오래된 백업 파일 삭제: {backup_file}")
    except Exception as e:
        print(f"백업 정리 중 오류: {str(e)}")

def should_create_backup():
    """오늘 백업이 필요한지 확인"""
    today = datetime.now().strftime("%Y-%m-%d")
    today_mappings_backup = BACKUP_DIR / f"port_mappings_{today}.json"
    today_servers_backup = BACKUP_DIR / f"servers_{today}.json"
    today_users_backup = BACKUP_DIR / f"users_{today}.json"
    
    # 모든 파일이 존재하면 백업 불필요
    return not (today_mappings_backup.exists() and today_servers_backup.exists() and today_users_backup.exists())

def get_backup_status():
    """백업 상태 정보 반환"""
    backups = []
    for backup_file in sorted(BACKUP_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        file_mtime = datetime.fromtimestamp(backup_file.stat().st_mtime)
        file_size = backup_file.stat().st_size
        backups.append({
            'filename': backup_file.name,
            'date': file_mtime.strftime("%Y-%m-%d %H:%M:%S"),
            'size': f"{file_size / 1024:.1f} KB"
        })
    return backups

def authenticate_user(username, password):
    """사용자 인증"""
    try:
        users = load_data(USERS_FILE)
        for user in users:
            if user['username'] == username and user['password'] == password:
                # 마지막 로그인 시간 업데이트
                user['last_login'] = datetime.now().isoformat()
                save_data(USERS_FILE, users)
                return True, user
        return False, None
    except Exception as e:
        return False, None

def is_authenticated():
    """로그인 상태 확인"""
    return st.session_state.get('authenticated', False)

def get_current_user():
    """현재 로그인한 사용자 정보 반환"""
    return st.session_state.get('current_user', None)

def logout():
    """로그아웃"""
    st.session_state.authenticated = False
    st.session_state.current_user = None
    # 쿠키 삭제
    if 'login_token' in st.session_state:
        del st.session_state.login_token
    st.rerun()

def create_login_token(username, role):
    """로그인 토큰 생성"""
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    token_data = f"{username}:{role}:{timestamp}"
    return base64.b64encode(token_data.encode()).decode()

def validate_login_token(token):
    """로그인 토큰 검증"""
    try:
        decoded = base64.b64decode(token.encode()).decode()
        username, role, timestamp = decoded.split(":")
        
        users = load_data(USERS_FILE)
        for user in users:
            if user['username'] == username and user['role'] == role:
                return True, user
        return False, None
    except:
        return False, None

def check_persistent_login():
    """지속적인 로그인 상태 확인"""
    if 'login_token' in st.session_state:
        token = st.session_state.login_token
        is_valid, user = validate_login_token(token)
        if is_valid:
            st.session_state.authenticated = True
            st.session_state.current_user = user
            return True
    return False

def change_password(username, old_password, new_password):
    """비밀번호 변경"""
    try:
        users = load_data(USERS_FILE)
        for user in users:
            if user['username'] == username and user['password'] == old_password:
                user['password'] = new_password
                user['updated_at'] = datetime.now().isoformat()
                save_data(USERS_FILE, users)
                return True, "비밀번호가 성공적으로 변경되었습니다."
        return False, "현재 비밀번호가 올바르지 않습니다."
    except Exception as e:
        return False, f"비밀번호 변경 중 오류가 발생했습니다: {str(e)}"

def add_new_user(username, password, role="user"):
    """새 사용자 추가"""
    try:
        users = load_data(USERS_FILE)
        
        # 사용자명 중복 확인
        if any(user['username'] == username for user in users):
            return False, "이미 존재하는 사용자명입니다."
        
        new_user = {
            'id': get_next_id(users),
            'username': username,
            'password': password,
            'role': role,
            'created_at': datetime.now().isoformat(),
            'last_login': None
        }
        
        users.append(new_user)
        save_data(USERS_FILE, users)
        return True, "새 사용자가 성공적으로 추가되었습니다."
    except Exception as e:
        return False, f"사용자 추가 중 오류가 발생했습니다: {str(e)}"

def delete_user(user_id):
    """사용자 삭제"""
    try:
        users = load_data(USERS_FILE)
        users = [user for user in users if user['id'] != user_id]
        save_data(USERS_FILE, users)
        return True, "사용자가 성공적으로 삭제되었습니다."
    except Exception as e:
        return False, f"사용자 삭제 중 오류가 발생했습니다: {str(e)}"

def load_data(file_path):
    """JSON 파일에서 데이터 로드"""
    try:
        if file_path.exists():
            return json.loads(file_path.read_text())
        return []
    except Exception as e:
        st.error(f"데이터 로드 오류: {e}")
        return []

def save_data(file_path, data):
    """JSON 파일에 데이터 저장"""
    try:
        file_path.write_text(json.dumps(data, indent=2))
        return True
    except Exception as e:
        st.error(f"데이터 저장 오류: {e}")
        return False

def check_server_status(server_ip, port=80):
    """서버 상태 확인"""
    try:
        response = requests.get(f"http://{server_ip}:{port}", timeout=3)
        return response.status_code < 500
    except:
        return False

def check_server_status_advanced(server_ip, default_port=80, ports=[80, 443, 8080, 3000, 5000]):
    """다양한 포트로 서버 상태 확인"""
    # 기본 포트를 먼저 확인
    if default_port not in ports:
        ports.insert(0, default_port)
    
    for port in ports:
        try:
            response = requests.get(f"http://{server_ip}:{port}", timeout=2)
            if response.status_code < 500:
                return True, port
        except:
            continue
    return False, None

def get_next_id(data_list):
    """다음 ID 생성"""
    if not data_list:
        return 1
    return max(item.get('id', 0) for item in data_list) + 1

# 데이터 초기화
initialize_data_files()

# 세션 상태 초기화
if 'initialized' not in st.session_state:
    st.session_state.initialized = True
    st.session_state.mappings = load_data(MAPPINGS_FILE)
    st.session_state.servers = load_data(SERVERS_FILE)
    st.session_state.users = load_data(USERS_FILE)
    
    # 포트포워더 초기화
    st.session_state.port_forwarder = PortForwarder(DATA_DIR)
    
    # 프록시 서버 시작
    if 'proxy_server' not in st.session_state:
        st.session_state.proxy_server = ProxyServer(DATA_DIR)
        st.session_state.proxy_server.start_background()
    
    # 자동 백업 체크 및 실행
    if should_create_backup():
        success, message = create_backup()
        if success:
            print(f"자동 백업: {message}")
        else:
            print(f"자동 백업 실패: {message}")
    
    # 오래된 백업 정리
    cleanup_old_backups()

# 로그인 상태 확인
if 'authenticated' not in st.session_state:
    st.session_state.authenticated = False
if 'current_user' not in st.session_state:
    st.session_state.current_user = None

if not st.session_state.authenticated:
    # 로그인 화면
    st.title("🔐 리버스 프록시 포트포워딩 플랫폼")
    st.markdown("---")
    
    # 로그인 폼
    with st.form("login_form"):
        st.subheader("로그인")
        
        username = st.text_input("사용자명", placeholder="사용자명을 입력하세요")
        password = st.text_input("비밀번호", type="password", placeholder="비밀번호를 입력하세요")
        
        col1, col2 = st.columns([1, 1])
        with col1:
            login_button = st.form_submit_button("로그인", type="primary")
        with col2:
            st.form_submit_button("취소")
        
        if login_button:
            if username and password:
                success, user = authenticate_user(username, password)
                if success:
                    st.session_state.authenticated = True
                    st.session_state.current_user = user
                    # 로그인 토큰 생성 및 저장
                    login_token = create_login_token(user['username'], user['role'])
                    st.session_state.login_token = login_token
                    st.success("로그인 성공!")
                    st.rerun()
                else:
                    st.error("사용자명 또는 비밀번호가 올바르지 않습니다.")
            else:
                st.warning("사용자명과 비밀번호를 모두 입력해주세요.")
    
    st.stop()  # 로그인하지 않으면 여기서 중단

# 로그인된 사용자만 접근 가능한 메인 화면
# 지속적인 로그인 상태 확인
if not st.session_state.authenticated:
    if check_persistent_login():
        pass  # 토큰이 유효하면 계속 진행
    else:
        st.rerun()  # 토큰이 유효하지 않으면 로그인 화면으로

current_user = get_current_user()

# 포트포워더가 없으면 초기화 (이전 버전 호환성)
if 'port_forwarder' not in st.session_state:
    st.session_state.port_forwarder = PortForwarder(DATA_DIR)

# 사이드바
with st.sidebar:
    st.title("🔗 포트포워딩 플랫폼")
    
    # 로그인 정보 표시
    if current_user:
        st.success(f"👤 {current_user['username']} ({current_user['role']})")
        if current_user.get('last_login'):
            last_login = datetime.fromisoformat(current_user['last_login']).strftime("%Y-%m-%d %H:%M")
            st.caption(f"마지막 로그인: {last_login}")
        
        # 비밀번호 변경 버튼
        if st.button("🔑 비밀번호 변경"):
            st.session_state.show_change_password = True
        
        if st.button("🚪 로그아웃"):
            logout()
    
    st.markdown("---")
    
    # 서버 상태
    st.subheader("📊 서버 상태")
    
    # 프록시 서버 상태
    try:
        proxy_health = requests.get(f"http://localhost:{st.session_state.proxy_server.current_port}/health", timeout=2)
        if proxy_health.status_code == 200:
            st.success("🟢 프록시 서버 정상")
        else:
            st.error("🔴 프록시 서버 오류")
    except:
        st.error("🔴 프록시 서버 연결 실패")
    
    # 포트포워딩 상태
    port_status = st.session_state.port_forwarder.get_status()
    active_forwards = sum(1 for forward in port_status.get('forwards', []) if forward.get('is_active', False))
    total_forwards = len(port_status.get('forwards', []))
    
    if total_forwards > 0:
        st.info(f"🔗 포트포워딩: {active_forwards}/{total_forwards} 활성")
    else:
        st.info("🔗 포트포워딩: 설정 없음")
    
    st.markdown("---")
    
    # 수동 새로고침
    if st.button("🔄 새로고침"):
        st.rerun()
    
    st.markdown("---")
    
    # 빠른 추가 버튼
    st.subheader("➕ 빠른 추가")
    
    col1, col2 = st.columns(2)
    with col1:
        if st.button("🔗 포트 추가", use_container_width=True):
            st.session_state.show_add_mapping = True
    
    with col2:
        if st.button("🖥️ 서버 추가", use_container_width=True):
            st.session_state.show_add_server = True
    
    # 비밀번호 변경 폼
    if st.session_state.get('show_change_password', False):
        with st.form("change_password_form"):
            st.subheader("🔑 비밀번호 변경")
            
            current_password = st.text_input("현재 비밀번호", type="password")
            new_password = st.text_input("새 비밀번호", type="password")
            confirm_password = st.text_input("새 비밀번호 확인", type="password")
            
            col1, col2 = st.columns(2)
            with col1:
                if st.form_submit_button("변경"):
                    if current_password and new_password and confirm_password:
                        if new_password == confirm_password:
                            success, message = change_password(current_user['username'], current_password, new_password)
                            if success:
                                st.success(message)
                                st.session_state.show_change_password = False
                                st.rerun()
                            else:
                                st.error(message)
                        else:
                            st.error("새 비밀번호가 일치하지 않습니다.")
                    else:
                        st.warning("모든 필드를 입력해주세요.")
            
            with col2:
                if st.form_submit_button("취소"):
                    st.session_state.show_change_password = False
                    st.rerun()
    
    # 사이드바 하단 정보
    st.markdown("---")
    
    # 버전 정보
    st.markdown("""
    <div style="
        background-color: rgba(255, 255, 255, 0.1);
        padding: 10px;
        border-radius: 5px;
        border: 1px solid rgba(255, 255, 255, 0.2);
        margin: 10px 0;
    ">
        <div style="text-align: center; color: #f0f2f6;">
            <strong>📦 v1.0.0</strong><br>
            <small>Streamlit 기반</small>
        </div>
    </div>
    """, unsafe_allow_html=True)
    
    # 깃허브 링크
    st.markdown("""
    <div style="
        background-color: rgba(255, 255, 255, 0.1);
        padding: 10px;
        border-radius: 5px;
        border: 1px solid rgba(255, 255, 255, 0.2);
        margin: 10px 0;
    ">
        <div style="text-align: center;">
            <a href="https://github.com/your-username/reverse-proxy-streamlit" 
               target="_blank" 
               style="
                   color: #00d4aa;
                   text-decoration: none;
                   font-weight: bold;
               ">
                🐙 GitHub
            </a>
        </div>
    </div>
    """, unsafe_allow_html=True)

# 메인 컨텐츠
st.title("리버스 프록시 포트포워딩 플랫폼")

# 탭 생성
tab1, tab2, tab3, tab4, tab5 = st.tabs(["포트 매핑", "서버 관리", "포트 포워딩", "백업 관리", "사용자 관리"])

with tab1:
    st.header("포트 매핑 관리")
    
    # 매핑 테이블
    if st.session_state.mappings:
        # 필터링
        col1, col2 = st.columns([2, 1])
        with col1:
            search = st.text_input("🔍 검색", placeholder="포트 또는 설명으로 검색")
        with col2:
            status_filter = st.selectbox("상태", ["전체", "활성", "비활성"])
        
        # 필터링된 데이터
        filtered_mappings = st.session_state.mappings
        if search:
            filtered_mappings = [m for m in filtered_mappings 
                               if search in str(m.get('external_port', '')) 
                               or search.lower() in m.get('description', '').lower()]
        
        if status_filter == "활성":
            filtered_mappings = [m for m in filtered_mappings if m.get('is_active', True)]
        elif status_filter == "비활성":
            filtered_mappings = [m for m in filtered_mappings if not m.get('is_active', True)]
        
        # 테이블 표시
        for i, mapping in enumerate(filtered_mappings):
            with st.container():
                col1, col2, col3, col4, col5, col6 = st.columns([1, 2, 1, 2, 1, 1])
                
                with col1:
                    st.write(f"**{mapping['external_port']}**")
                
                with col2:
                    st.write(f"→ {mapping['target_server']}:{mapping['target_port']}")
                
                with col3:
                    status = "🟢 활성" if mapping.get('is_active', True) else "🔴 비활성"
                    st.write(status)
                
                with col4:
                    st.write(mapping.get('description', ''))
                
                with col5:
                    if st.button("삭제", key=f"del_{mapping['id']}_{i}"):
                        st.session_state.mappings = [m for m in st.session_state.mappings if m['id'] != mapping['id']]
                        save_data(MAPPINGS_FILE, st.session_state.mappings)
                        
                        # 포트포워딩 재시작
                        st.session_state.port_forwarder.reload_mappings()
                        
                        st.rerun()
                
                with col6:
                    if st.button("✏️ 수정", key=f"edit_{mapping['id']}_{i}"):
                        st.session_state.editing_mapping = mapping['id']
                        st.rerun()
                
                st.divider()
    else:
        st.info("등록된 포트 매핑이 없습니다.")
    
    # 새 매핑 추가 모달
    if st.session_state.get('show_add_mapping', False):
        with st.form("add_mapping_form"):
            st.subheader("새 포트 매핑 추가")
            
            col1, col2 = st.columns(2)
            with col1:
                external_port = st.number_input("외부 포트", min_value=1, max_value=65535, value=8080)
                target_server = st.selectbox("대상 서버", 
                                           [s['name'] for s in st.session_state.servers],
                                           format_func=lambda x: x)
            
            with col2:
                target_port = st.number_input("대상 포트", min_value=1, max_value=65535, value=80)
                description = st.text_input("설명")
            
            col1, col2 = st.columns(2)
            with col1:
                submitted = st.form_submit_button("추가")
            with col2:
                cancelled = st.form_submit_button("취소")
            
            if submitted:
                if st.session_state.servers:
                    server_ip = next(s['ip'] for s in st.session_state.servers if s['name'] == target_server)
                    
                    new_mapping = {
                        'id': get_next_id(st.session_state.mappings),
                        'external_port': external_port,
                        'target_server': server_ip,
                        'target_port': target_port,
                        'description': description,
                        'is_active': True,
                        'created_at': datetime.now().isoformat()
                    }
                    
                    st.session_state.mappings.append(new_mapping)
                    save_data(MAPPINGS_FILE, st.session_state.mappings)
                    
                    # 프록시 서버에 매핑 변경 알림
                    current_port = getattr(st.session_state.proxy_server, 'current_port', None)
                    if current_port:
                        try:
                            requests.post(f"http://localhost:{current_port}/reload", timeout=2)
                        except:
                            pass
                    
                    # 포트포워딩 재시작
                    st.session_state.port_forwarder.reload_mappings()
                    
                    st.session_state.show_add_mapping = False
                    st.rerun()
                else:
                    st.error("먼저 서버를 추가해주세요.")
            
            if cancelled:
                st.session_state.show_add_mapping = False
                st.rerun()
    
    # 포트 매핑 수정 모달
    if st.session_state.get('editing_mapping'):
        editing_id = st.session_state.editing_mapping
        editing_mapping = next((m for m in st.session_state.mappings if m['id'] == editing_id), None)
        
        if editing_mapping:
            with st.form("edit_mapping_form"):
                st.subheader(f"포트 매핑 수정 (ID: {editing_id})")
                
                col1, col2 = st.columns(2)
                with col1:
                    external_port = st.number_input("외부 포트", min_value=1, max_value=65535, value=editing_mapping['external_port'], key=f"edit_external_{editing_id}")
                    target_server = st.selectbox("대상 서버", 
                                               [s['name'] for s in st.session_state.servers],
                                               index=next((i for i, s in enumerate(st.session_state.servers) if s['ip'] == editing_mapping['target_server']), 0),
                                               key=f"edit_server_{editing_id}")
                
                with col2:
                    target_port = st.number_input("대상 포트", min_value=1, max_value=65535, value=editing_mapping['target_port'], key=f"edit_target_{editing_id}")
                    description = st.text_input("설명", value=editing_mapping.get('description', ''), key=f"edit_desc_{editing_id}")
                    is_active = st.checkbox("활성 상태", value=editing_mapping.get('is_active', True), key=f"edit_active_{editing_id}")
                
                col1, col2 = st.columns(2)
                with col1:
                    if st.form_submit_button("수정"):
                        if st.session_state.servers:
                            server_ip = next(s['ip'] for s in st.session_state.servers if s['name'] == target_server)
                            
                            # 매핑 업데이트
                            editing_mapping.update({
                                'external_port': external_port,
                                'target_server': server_ip,
                                'target_port': target_port,
                                'description': description,
                                'is_active': is_active,
                                'updated_at': datetime.now().isoformat()
                            })
                            
                            save_data(MAPPINGS_FILE, st.session_state.mappings)
                            
                            # 포트포워딩 재시작
                            st.session_state.port_forwarder.reload_mappings()
                            
                            st.session_state.editing_mapping = None
                            st.success("포트 매핑이 수정되었습니다!")
                            st.rerun()
                        else:
                            st.error("먼저 서버를 추가해주세요.")
                
                with col2:
                    if st.form_submit_button("취소"):
                        st.session_state.editing_mapping = None
                        st.rerun()

with tab2:
    st.header("서버 관리")
    
    # 서버 상태 디버깅
    if st.button("🔍 서버 상태 상세 확인"):
        st.subheader("서버 상태 상세 정보")
        for server in st.session_state.servers:
            with st.expander(f"서버: {server['name']} ({server['ip']})"):
                default_port = server.get('default_port', 80)
                st.write(f"기본 포트: {default_port}")
                
                # 각 포트별 상태 확인
                ports_to_check = [default_port, 80, 443, 8080, 3000, 5000]
                ports_to_check = list(dict.fromkeys(ports_to_check))  # 중복 제거
                
                for port in ports_to_check:
                    try:
                        response = requests.get(f"http://{server['ip']}:{port}", timeout=2)
                        if response.status_code < 500:
                            st.success(f"포트 {port}: 응답 코드 {response.status_code}")
                        else:
                            st.warning(f"포트 {port}: 응답 코드 {response.status_code}")
                    except Exception as e:
                        st.error(f"포트 {port}: 연결 실패 - {str(e)}")
    
    # 서버 목록
    if st.session_state.servers:
        for i, server in enumerate(st.session_state.servers):
            with st.container():
                col1, col2, col3, col4, col5 = st.columns([2, 2, 1, 1, 1])
                
                with col1:
                    st.write(f"**{server['name']}**")
                
                with col2:
                    st.write(f"`{server['ip']}`")
                
                with col3:
                    # 서버 상태 확인 (다양한 포트로 확인)
                    default_port = server.get('default_port', 80)
                    is_online, working_port = check_server_status_advanced(server['ip'], default_port)
                    if is_online:
                        status = f"🟢 온라인 (포트 {working_port})"
                    else:
                        status = "🔴 오프라인"
                    st.write(status)
                
                with col4:
                    if st.button("삭제", key=f"del_server_{server['id']}_{i}"):
                        st.session_state.servers = [s for s in st.session_state.servers if s['id'] != server['id']]
                        save_data(SERVERS_FILE, st.session_state.servers)
                        st.rerun()
                
                with col5:
                    if st.button("✏️ 수정", key=f"edit_server_{server['id']}_{i}"):
                        st.session_state.editing_server = server['id']
                        st.rerun()
                
                st.divider()
    else:
        st.info("등록된 서버가 없습니다.")
    
    # 새 서버 추가
    if st.session_state.get('show_add_server', False):
        with st.form("add_server_form"):
            st.subheader("새 서버 추가")
            
            col1, col2 = st.columns(2)
            with col1:
                server_name = st.text_input("서버 이름")
                server_ip = st.text_input("서버 IP", placeholder="예: 192.168.1.100")
            with col2:
                server_port = st.number_input("기본 포트", min_value=1, max_value=65535, value=80, help="서버 상태 확인용 기본 포트")
                st.caption("다른 포트도 자동으로 확인됩니다")
            
            submitted = st.form_submit_button("추가")
            if submitted and server_name and server_ip:
                new_server = {
                    'id': get_next_id(st.session_state.servers),
                    'name': server_name,
                    'ip': server_ip,
                    'default_port': server_port,
                    'created_at': datetime.now().isoformat()
                }
                
                st.session_state.servers.append(new_server)
                save_data(SERVERS_FILE, st.session_state.servers)
                st.session_state.show_add_server = False
                st.rerun()
    
    # 서버 수정 모달
    if st.session_state.get('editing_server'):
        editing_id = st.session_state.editing_server
        editing_server = next((s for s in st.session_state.servers if s['id'] == editing_id), None)
        
        if editing_server:
            with st.form("edit_server_form"):
                st.subheader(f"서버 수정 (ID: {editing_id})")
                
                col1, col2 = st.columns(2)
                with col1:
                    server_name = st.text_input("서버 이름", value=editing_server['name'], key=f"edit_name_{editing_id}")
                    server_ip = st.text_input("서버 IP", value=editing_server['ip'], key=f"edit_ip_{editing_id}")
                with col2:
                    default_port = st.number_input("기본 포트", min_value=1, max_value=65535, value=editing_server.get('default_port', 80), key=f"edit_port_{editing_id}")
                    st.caption("서버 상태 확인용 기본 포트")
                
                col1, col2 = st.columns(2)
                with col1:
                    if st.form_submit_button("수정"):
                        if server_name and server_ip:
                            # 서버 정보 업데이트
                            editing_server.update({
                                'name': server_name,
                                'ip': server_ip,
                                'default_port': default_port,
                                'updated_at': datetime.now().isoformat()
                            })
                            
                            save_data(SERVERS_FILE, st.session_state.servers)
                            
                            st.session_state.editing_server = None
                            st.success("서버 정보가 수정되었습니다!")
                            st.rerun()
                        else:
                            st.error("서버 이름과 IP를 입력해주세요.")
                
                with col2:
                    if st.form_submit_button("취소"):
                        st.session_state.editing_server = None
                        st.rerun()

with tab3:
    st.header("포트포워딩 상태")
    
    # 포트포워딩 상태 표시
    forwarder_status = st.session_state.port_forwarder.get_status()
    
    col1, col2 = st.columns(2)
    with col1:
        st.metric("실행 상태", "🟢 실행 중" if forwarder_status['is_running'] else "🔴 중지됨")
    with col2:
        st.metric("활성 포워딩", forwarder_status['active_forwards'])
    
    # 포워딩 목록
    if forwarder_status['forwards']:
        st.subheader("활성 포트포워딩")
        for forward in forwarder_status['forwards']:
            with st.container():
                col1, col2, col3, col4, col5 = st.columns([2, 3, 1, 1, 1])
                
                with col1:
                    st.write(f"**포트 {forward['external_port']}**")
                
                with col2:
                    st.write(f"→ {forward['target_host']}:{forward['target_port']}")
                
                with col3:
                    status = "🟢 활성" if forward['is_active'] else "🔴 비활성"
                    st.write(status)
                
                with col4:
                    st.write(f"연결: {forward['connections']}")
                
                with col5:
                    # 상세 상태 표시
                    thread_status = "✅" if forward['thread_alive'] else "❌"
                    socket_status = "✅" if forward['server_socket_bound'] else "❌"
                    st.write(f"스레드: {thread_status}")
                    st.write(f"소켓: {socket_status}")
                
                st.divider()
    else:
        st.info("활성 포트포워딩이 없습니다.")
    
    # 포트포워딩 제어
    st.subheader("포트포워딩 제어")
    col1, col2 = st.columns(2)
    
    with col1:
        if st.button("🟢 모든 포트포워딩 시작", use_container_width=True):
            st.session_state.port_forwarder.start_all_forwards()
            
            # 상태 확인을 위해 잠시 대기
            time.sleep(0.5)
            
            # 상태 재확인
            status = st.session_state.port_forwarder.get_status()
            if status['is_running']:
                st.success(f"모든 포트포워딩을 시작했습니다! (활성: {status['active_forwards']}개)")
            else:
                st.error("포트포워딩 시작에 실패했습니다.")
            st.rerun()
    
    with col2:
        if st.button("🔴 모든 포트포워딩 중지", use_container_width=True):
            st.session_state.port_forwarder.stop_all_forwards()
            st.success("모든 포트포워딩을 중지했습니다!")
            st.rerun()
    
    # 디버깅 정보
    st.subheader("디버깅 정보")
    if st.button("🔍 포트포워딩 상태 상세 확인"):
        with st.expander("상세 상태 정보"):
            for forward in forwarder_status['forwards']:
                st.write(f"**포트 {forward['external_port']}:**")
                st.write(f"  - 대상: {forward['target_host']}:{forward['target_port']}")
                st.write(f"  - 활성 상태: {forward['is_active']}")
                st.write(f"  - 스레드 실행: {forward['thread_alive']}")
                st.write(f"  - 소켓 바인딩: {forward['server_socket_bound']}")
                st.write(f"  - 연결 수: {forward['connections']}")
                st.write("---")

with tab4:
    st.header("📦 백업 관리")
    
    # 백업 상태 표시
    st.subheader("백업 상태")
    
    col1, col2 = st.columns(2)
    with col1:
        if st.button("🔄 수동 백업 생성"):
            success, message = create_backup()
            if success:
                st.success(message)
            else:
                st.error(message)
            st.rerun()
    
    with col2:
        if st.button("🧹 오래된 백업 정리"):
            cleanup_old_backups()
            st.success("오래된 백업 파일이 정리되었습니다!")
            st.rerun()
    
    # 백업 정보 표시
    st.subheader("백업 파일 목록")
    backups = get_backup_status()
    
    if backups:
        # 백업 파일 테이블
        backup_data = []
        for backup in backups:
            backup_data.append([
                backup['filename'],
                backup['date'],
                backup['size']
            ])
        
        st.table({
            "파일명": [row[0] for row in backup_data],
            "생성일시": [row[1] for row in backup_data],
            "크기": [row[2] for row in backup_data]
        })
        
        # 백업 통계
        st.subheader("📊 백업 통계")
        col1, col2, col3 = st.columns(3)
        
        with col1:
            st.metric("총 백업 파일", len(backups))
        
        with col2:
            total_size = sum(float(backup['size'].replace(' KB', '')) for backup in backups)
            st.metric("총 크기", f"{total_size:.1f} KB")
        
        with col3:
            if backups:
                latest_backup = backups[0]['date']
                st.metric("최신 백업", latest_backup.split()[0])
            else:
                st.metric("최신 백업", "없음")
    
    else:
        st.info("아직 백업 파일이 없습니다.")
    
    # 백업 설정 정보
    st.subheader("⚙️ 백업 설정")
    st.info(f"""
    - **백업 경로**: `{BACKUP_DIR}`
    - **보관 기간**: {BACKUP_RETENTION_DAYS}일
    - **자동 백업**: 매일 자정에 자동 실행
    - **백업 파일**: 
      - `port_mappings_YYYY-MM-DD.json`
      - `servers_YYYY-MM-DD.json`
      - `users_YYYY-MM-DD.json`
    """)

with tab5:
    st.header("👥 사용자 관리")
    
    # 새 사용자 추가 버튼
    if st.button("➕ 새 사용자 추가"):
        st.session_state.show_add_user = True
        st.rerun()
    
    # 사용자 목록
    if st.session_state.users:
        for i, user in enumerate(st.session_state.users):
            with st.container():
                col1, col2, col3, col4, col5 = st.columns([2, 1, 1, 1, 1])
                
                with col1:
                    st.write(f"**{user['username']}**")
                
                with col2:
                    st.write(f"`{user['role']}`")
                
                with col3:
                    if user.get('last_login'):
                        last_login = datetime.fromisoformat(user['last_login']).strftime("%Y-%m-%d %H:%M")
                        st.caption(f"마지막 로그인: {last_login}")
                    else:
                        st.caption("로그인 기록 없음")
                
                with col4:
                    if st.button("삭제", key=f"del_user_{user['id']}_{i}"):
                        if len(st.session_state.users) > 1:  # 최소 1명은 남겨두기
                            st.session_state.users = [u for u in st.session_state.users if u['id'] != user['id']]
                            save_data(USERS_FILE, st.session_state.users)
                            st.success("사용자가 삭제되었습니다!")
                            st.rerun()
                        else:
                            st.error("최소 1명의 사용자는 남겨두어야 합니다.")
                
                with col5:
                    if st.button("✏️ 수정", key=f"edit_user_{user['id']}_{i}"):
                        st.session_state.editing_user = user['id']
                        st.rerun()
                
                st.divider()
    else:
        st.info("등록된 사용자가 없습니다.")
    
    # 새 사용자 추가
    if st.session_state.get('show_add_user', False):
        with st.form("add_user_form"):
            st.subheader("새 사용자 추가")
            
            col1, col2 = st.columns(2)
            with col1:
                username = st.text_input("사용자명")
                password = st.text_input("비밀번호", type="password")
                role = st.selectbox("역할", ["user", "admin"])
            
            submitted = st.form_submit_button("추가")
            if submitted and username and password:
                success, message = add_new_user(username, password, role)
                if success:
                    st.success(message)
                else:
                    st.error(message)
                st.rerun()
            if st.form_submit_button("취소"):
                st.session_state.show_add_user = False
                st.rerun()
    
    # 사용자 수정 모달
    if st.session_state.get('editing_user'):
        editing_id = st.session_state.editing_user
        editing_user = next((u for u in st.session_state.users if u['id'] == editing_id), None)
        
        if editing_user:
            with st.form("edit_user_form"):
                st.subheader(f"사용자 수정 (ID: {editing_id})")
                
                col1, col2 = st.columns(2)
                with col1:
                    username = st.text_input("사용자명", value=editing_user['username'], key=f"edit_username_{editing_id}")
                    password = st.text_input("비밀번호", type="password", value="", key=f"edit_password_{editing_id}")
                    role = st.selectbox("역할", ["user", "admin"], index=next((i for i, r in enumerate(["user", "admin"]) if r == editing_user['role']), 0), key=f"edit_role_{editing_id}")
                    is_active = st.checkbox("활성 상태", value=editing_user.get('is_active', True), key=f"edit_active_{editing_id}")
                
                col1, col2 = st.columns(2)
                with col1:
                    if st.form_submit_button("수정"):
                        if username:
                            # 사용자 정보 업데이트
                            editing_user.update({
                                'username': username,
                                'role': role,
                                'is_active': is_active,
                                'updated_at': datetime.now().isoformat()
                            })
                            
                            if password: # 비밀번호가 입력되었으면 변경
                                success, message = change_password(username, editing_user['password'], password)
                                if success:
                                    st.success("비밀번호가 변경되었습니다.")
                                else:
                                    st.error(message)
                            
                            save_data(USERS_FILE, st.session_state.users)
                            
                            st.session_state.editing_user = None
                            st.success("사용자 정보가 수정되었습니다!")
                            st.rerun()
                        else:
                            st.error("사용자명을 입력해주세요.")
                
                with col2:
                    if st.form_submit_button("취소"):
                        st.session_state.editing_user = None
                        st.rerun()

# 수동 새로고침
if st.button("🔄 새로고침"):
    st.rerun()

# 자동 새로고침 (더 안정적인 방식)
if 'last_refresh' not in st.session_state:
    st.session_state.last_refresh = time.time()

# 15초마다 새로고침 (더 긴 간격으로 안정성 향상)
current_time = time.time()
if current_time - st.session_state.last_refresh > 15:
    st.session_state.last_refresh = current_time
    # 새로고침 전에 상태 확인
    try:
        # 포트포워딩 상태 확인
        forwarder_status = st.session_state.port_forwarder.get_status()
        if not forwarder_status['is_running']:
            st.session_state.port_forwarder.start_all_forwards()
    except:
        pass
    st.rerun() 