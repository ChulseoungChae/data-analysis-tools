import asyncio
import aiohttp
from aiohttp import web, ClientSession, TCPConnector
import json
import logging
from pathlib import Path
from datetime import datetime
import threading
import time

# 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class ProxyServer:
    def __init__(self, data_dir="/mnt/data"):
        self.data_dir = Path(data_dir)
        self.mappings_file = self.data_dir / "port_mappings.json"
        self.servers_file = self.data_dir / "servers.json"
        self.app = web.Application()
        self.routes = {}
        self.is_running = False
        self.server = None
        
        # 라우트 설정
        self.setup_routes()
    
    def setup_routes(self):
        """프록시 라우트 설정"""
        self.app.router.add_get('/health', self.health_check)
        self.app.router.add_get('/status', self.get_status)
        self.app.router.add_post('/reload', self.reload_mappings)
    
    async def health_check(self, request):
        """헬스 체크 엔드포인트"""
        return web.json_response({
            'status': 'healthy',
            'timestamp': datetime.now().isoformat(),
            'active_mappings': len(self.routes)
        })
    
    async def get_status(self, request):
        """상태 정보 엔드포인트"""
        return web.json_response({
            'status': 'running' if self.is_running else 'stopped',
            'active_mappings': len(self.routes),
            'mappings': list(self.routes.keys()),
            'timestamp': datetime.now().isoformat()
        })
    
    async def reload_mappings(self, request):
        """매핑 설정 재로드"""
        await self.load_mappings()
        return web.json_response({
            'status': 'reloaded',
            'active_mappings': len(self.routes),
            'timestamp': datetime.now().isoformat()
        })
    
    def load_mappings(self):
        """매핑 설정 로드"""
        try:
            if self.mappings_file.exists():
                mappings = json.loads(self.mappings_file.read_text())
                self.routes = {}
                
                for mapping in mappings:
                    if mapping.get('is_active', True):
                        external_port = mapping['external_port']
                        target_url = f"http://{mapping['target_server']}:{mapping['target_port']}"
                        self.routes[external_port] = target_url
                        logger.info(f"매핑 로드: {external_port} -> {target_url}")
                
                logger.info(f"총 {len(self.routes)}개의 활성 매핑을 로드했습니다.")
            else:
                logger.warning("매핑 파일이 존재하지 않습니다.")
        except Exception as e:
            logger.error(f"매핑 로드 오류: {e}")
    
    async def proxy_handler(self, request):
        """프록시 요청 처리"""
        path = request.path
        method = request.method
        headers = dict(request.headers)
        
        # 호스트 헤더 제거 (프록시 서버에서 처리)
        headers.pop('Host', None)
        
        # 요청 본문 읽기
        body = await request.read() if request.body_exists else None
        
        # 타겟 URL 결정
        target_url = None
        for port, url in self.routes.items():
            if request.port == port:
                target_url = url + path
                break
        
        if not target_url:
            return web.Response(
                text="포트 매핑을 찾을 수 없습니다.",
                status=404
            )
        
        try:
            # 프록시 요청 전송
            async with ClientSession(connector=TCPConnector(limit=100)) as session:
                async with session.request(
                    method=method,
                    url=target_url,
                    headers=headers,
                    data=body,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    
                    # 응답 헤더 복사
                    response_headers = dict(response.headers)
                    
                    # 응답 본문 읽기
                    content = await response.read()
                    
                    logger.info(f"프록시 요청: {method} {path} -> {target_url} ({response.status})")
                    
                    return web.Response(
                        body=content,
                        status=response.status,
                        headers=response_headers
                    )
        
        except Exception as e:
            logger.error(f"프록시 요청 오류: {e}")
            return web.Response(
                text=f"프록시 오류: {str(e)}",
                status=500
            )
    
    async def start_server(self, host='0.0.0.0', port=8080):
        """프록시 서버 시작"""
        if self.is_running:
            logger.warning("서버가 이미 실행 중입니다.")
            return
        
        # 매핑 로드
        self.load_mappings()
        
        # 동적 라우트 설정
        for external_port in self.routes.keys():
            # 모든 경로에 대해 프록시 핸들러 등록
            self.app.router.add_route('*', f'/{external_port}{{path:.*}}', self.proxy_handler)
        
        # 서버 시작
        runner = web.AppRunner(self.app)
        await runner.setup()
        
        self.server = web.TCPSite(runner, host, port)
        await self.server.start()
        
        self.is_running = True
        logger.info(f"프록시 서버가 {host}:{port}에서 시작되었습니다.")
        logger.info(f"활성 매핑: {list(self.routes.keys())}")
    
    async def stop_server(self):
        """프록시 서버 중지"""
        if not self.is_running:
            logger.warning("서버가 실행 중이 아닙니다.")
            return
        
        if self.server:
            await self.server.stop()
        
        self.is_running = False
        logger.info("프록시 서버가 중지되었습니다.")
    
    def find_available_port(self, start_port=8080, max_attempts=10):
        """사용 가능한 포트 찾기"""
        import socket
        
        for port in range(start_port, start_port + max_attempts):
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.bind(('localhost', port))
                    return port
            except OSError:
                continue
        return None
    
    def start_background(self, host='0.0.0.0', port=8080):
        """백그라운드에서 서버 시작"""
        # 사용 가능한 포트 찾기
        available_port = self.find_available_port(port)
        if available_port is None:
            logger.error(f"포트 {port}부터 {port+10}까지 모두 사용 중입니다.")
            return False
        
        if available_port != port:
            logger.info(f"포트 {port}가 사용 중이어서 포트 {available_port}를 사용합니다.")
        
        def run_server():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(self.start_server(host, available_port))
                loop.run_forever()
            except Exception as e:
                logger.error(f"서버 시작 오류: {e}")
        
        self.server_thread = threading.Thread(target=run_server, daemon=True)
        self.server_thread.start()
        self.current_port = available_port
        logger.info(f"백그라운드에서 프록시 서버를 포트 {available_port}에서 시작했습니다.")
        return True
    
    def stop_background(self):
        """백그라운드 서버 중지"""
        if hasattr(self, 'server_thread') and self.server_thread.is_alive():
            # 서버 중지 로직 구현 필요
            logger.info("백그라운드 서버 중지 요청됨")

# 사용 예시
if __name__ == "__main__":
    proxy = ProxyServer()
    proxy.start_background(host='0.0.0.0', port=8080)
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        proxy.stop_background()
        print("서버가 중지되었습니다.") 