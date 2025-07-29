import socket
import threading
import logging
import time
from datetime import datetime
from pathlib import Path
import json
import select
import queue

# 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class PortForwarder:
    def __init__(self, data_dir="/mnt/data"):
        self.data_dir = Path(data_dir)
        self.mappings_file = self.data_dir / "port_mappings.json"
        self.active_forwards = {}  # {external_port: ForwardThread}
        self.is_running = False
        
    def load_mappings(self):
        """매핑 설정 로드"""
        try:
            if self.mappings_file.exists():
                mappings = json.loads(self.mappings_file.read_text())
                return [m for m in mappings if m.get('is_active', True)]
            return []
        except Exception as e:
            logger.error(f"매핑 로드 오류: {e}")
            return []
    
    def start_forwarding(self, external_port, target_host, target_port):
        """포트포워딩 시작"""
        if external_port in self.active_forwards:
            logger.warning(f"포트 {external_port}는 이미 포워딩 중입니다.")
            return False
        
        try:
            forward_thread = ForwardThread(external_port, target_host, target_port)
            forward_thread.start()
            
            # 스레드가 실제로 시작될 때까지 잠시 대기
            time.sleep(0.1)
            
            self.active_forwards[external_port] = forward_thread
            logger.info(f"포트포워딩 시작: {external_port} -> {target_host}:{target_port}")
            return True
        except Exception as e:
            logger.error(f"포트포워딩 시작 실패: {e}")
            return False
    
    def stop_forwarding(self, external_port):
        """포트포워딩 중지"""
        if external_port in self.active_forwards:
            self.active_forwards[external_port].stop()
            del self.active_forwards[external_port]
            logger.info(f"포트포워딩 중지: {external_port}")
            return True
        return False
    
    def start_all_forwards(self):
        """모든 활성 매핑에 대해 포트포워딩 시작"""
        mappings = self.load_mappings()
        for mapping in mappings:
            external_port = mapping['external_port']
            target_server = mapping['target_server']
            target_port = mapping['target_port']
            self.start_forwarding(external_port, target_server, target_port)
        
        self.is_running = True
        logger.info(f"총 {len(mappings)}개의 포트포워딩을 시작했습니다.")
    
    def stop_all_forwards(self):
        """모든 포트포워딩 중지"""
        for external_port in list(self.active_forwards.keys()):
            self.stop_forwarding(external_port)
        self.is_running = False
        logger.info("모든 포트포워딩을 중지했습니다.")
    
    def get_status(self):
        """포트포워딩 상태 반환"""
        status = {
            'is_running': self.is_running,
            'active_forwards': len(self.active_forwards),
            'forwards': []
        }
        
        for external_port, thread in self.active_forwards.items():
            # 스레드가 실제로 실행 중인지 확인
            is_thread_active = thread.is_alive() and thread.is_active
            
            status['forwards'].append({
                'external_port': external_port,
                'target_host': thread.target_host,
                'target_port': thread.target_port,
                'is_active': is_thread_active,
                'connections': thread.connection_count,
                'thread_alive': thread.is_alive(),
                'server_socket_bound': thread.server_socket is not None
            })
        
        return status
    
    def reload_mappings(self):
        """매핑 설정 재로드"""
        logger.info("매핑 설정 재로드 중...")
        
        # 현재 활성 포워딩 중지
        self.stop_all_forwards()
        
        # 새로운 매핑으로 포워딩 시작
        self.start_all_forwards()
        
        return self.get_status()


class ForwardThread(threading.Thread):
    def __init__(self, external_port, target_host, target_port):
        super().__init__()
        self.external_port = external_port
        self.target_host = target_host
        self.target_port = target_port
        self.is_active = False
        self.connection_count = 0
        self.server_socket = None
        self.daemon = True
        
        # 성능 최적화 설정
        self.buffer_size = 8192
        self.timeout = 30
    
    def run(self):
        """포트포워딩 스레드 실행"""
        try:
            # 서버 소켓 생성
            self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            self.server_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.server_socket.bind(('0.0.0.0', self.external_port))
            self.server_socket.listen(10)  # 백로그 증가
            self.server_socket.settimeout(1)  # 타임아웃 설정
            
            # 상태를 True로 설정 (소켓 바인딩 성공 후)
            self.is_active = True
            logger.info(f"포트포워딩 서버 시작 완료: {self.external_port} -> {self.target_host}:{self.target_port}")
            
            while self.is_active:
                try:
                    # 클라이언트 연결 대기 (타임아웃 포함)
                    client_socket, client_addr = self.server_socket.accept()
                    self.connection_count += 1
                    
                    # 새로운 스레드에서 연결 처리
                    connection_thread = ConnectionHandler(
                        client_socket, client_addr, 
                        self.target_host, self.target_port,
                        self.connection_count, self.buffer_size, self.timeout
                    )
                    connection_thread.start()
                    
                except socket.timeout:
                    # 타임아웃은 정상적인 상황
                    continue
                except Exception as e:
                    if self.is_active:
                        logger.error(f"연결 처리 오류: {e}")
                    break
                    
        except Exception as e:
            logger.error(f"포트포워딩 서버 시작 실패: {e}")
            self.is_active = False
        finally:
            if self.is_active:
                logger.info(f"포트포워딩 서버 종료: {self.external_port}")
            self.is_active = False
            if self.server_socket:
                self.server_socket.close()
    
    def stop(self):
        """포트포워딩 중지"""
        self.is_active = False
        if self.server_socket:
            self.server_socket.close()
        logger.info(f"포트포워딩 중지: {self.external_port}")


class ConnectionHandler(threading.Thread):
    def __init__(self, client_socket, client_addr, target_host, target_port, connection_id, buffer_size=8192, timeout=30):
        super().__init__()
        self.client_socket = client_socket
        self.client_addr = client_addr
        self.target_host = target_host
        self.target_port = target_port
        self.connection_id = connection_id
        self.buffer_size = buffer_size
        self.timeout = timeout
        self.daemon = True
    
    def run(self):
        """연결 처리"""
        target_socket = None
        try:
            # 대상 서버에 연결
            target_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            target_socket.settimeout(self.timeout)
            target_socket.connect((self.target_host, self.target_port))
            
            # 소켓 최적화 설정
            self.client_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            target_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            
            logger.info(f"연결 {self.connection_id}: {self.client_addr} -> {self.target_host}:{self.target_port}")
            
            # 양방향 데이터 전송 (개선된 방식)
            self.forward_data_optimized(self.client_socket, target_socket)
            
        except Exception as e:
            logger.error(f"연결 {self.connection_id} 오류: {e}")
        finally:
            # 소켓 정리
            if target_socket:
                target_socket.close()
            if self.client_socket:
                self.client_socket.close()
    
    def forward_data_optimized(self, source, destination):
        """개선된 양방향 데이터 전송"""
        def forward(src, dst, direction):
            try:
                while True:
                    # select를 사용한 비동기 읽기
                    ready = select.select([src], [], [], 1.0)[0]
                    if not ready:
                        continue
                    
                    data = src.recv(self.buffer_size)
                    if not data:
                        break
                    dst.send(data)
            except Exception as e:
                logger.debug(f"전송 오류 ({direction}): {e}")
        
        # 양방향 스레드 시작
        t1 = threading.Thread(target=forward, args=(source, destination, "client->target"))
        t2 = threading.Thread(target=forward, args=(destination, source, "target->client"))
        
        t1.daemon = True
        t2.daemon = True
        
        t1.start()
        t2.start()
        
        # 연결 유지 (타임아웃 포함)
        t1.join(timeout=self.timeout)
        t2.join(timeout=self.timeout)


# 사용 예시
if __name__ == "__main__":
    forwarder = PortForwarder()
    forwarder.start_all_forwards()
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        forwarder.stop_all_forwards()
        print("포트포워딩 서버가 중지되었습니다.") 