"""Local static welcome routes only; no settings, users, secrets, network."""
from pathlib import Path
import stat
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException

HEADERS={
    'Content-Security-Policy':"default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
    'Referrer-Policy':'no-referrer', 'X-Frame-Options':'DENY', 'X-Content-Type-Options':'nosniff',
    'Cache-Control':'no-store', 'Permissions-Policy':'camera=(), microphone=(), geolocation=()'}
ASSETS={'index.html','welcome.css','welcome.js','favicon.svg',
        'welcome-polish.css','welcome-polish.js',
        'fonts/InstrumentSans-latin-variable.woff2','fonts/OFL.txt','fonts/provenance.json'}
MEDIA_TYPES={'index.html':'text/html; charset=utf-8','welcome.css':'text/css; charset=utf-8',
             'welcome.js':'text/javascript; charset=utf-8','favicon.svg':'image/svg+xml',
             'welcome-polish.css':'text/css; charset=utf-8','welcome-polish.js':'text/javascript; charset=utf-8',
             'fonts/InstrumentSans-latin-variable.woff2':'font/woff2',
             'fonts/OFL.txt':'text/plain; charset=utf-8','fonts/provenance.json':'application/json'}

def safe_file(path,root):
    try:
        if path.is_symlink() or path.resolve()!=path or not path.is_relative_to(root):return None
        info=path.stat()
        return info if stat.S_ISREG(info.st_mode) else None
    except (OSError,ValueError):return None

class WelcomeFileResponse(FileResponse):
    async def __call__(self,scope,receive,send):
        started=False
        async def forward(message):
            nonlocal started
            if message['type']=='http.response.start':started=True
            await send(message)
        try:await super().__call__(scope,receive,forward)
        except (OSError,RuntimeError):
            if not started:await Response('Welcome unavailable',status_code=503,headers=HEADERS)(scope,receive,send)
            # A post-header local I/O failure ends the response; no exception
            # text or filesystem path is sent to the browser.

class WelcomeStatic(StaticFiles):
    async def get_response(self,path,scope):
        if path not in ASSETS:return Response('Not found',status_code=404,headers=HEADERS)
        requested=scope['path'];mount=scope.get('root_path','')
        if mount and requested.startswith(mount+'/'):requested=requested[len(mount):]
        if requested!='/'+path:return Response('Not found',status_code=404,headers=HEADERS)
        if safe_file(Path(self.directory)/path,Path(self.directory)) is None:
            return Response('Not found',status_code=404,headers=HEADERS)
        try:response=await super().get_response(path,scope)
        except HTTPException as error:response=Response('Not found' if error.status_code==404 else 'Method not allowed',status_code=error.status_code)
        except (OSError,RuntimeError):response=Response('Welcome unavailable',status_code=503)
        if response.status_code==200:response.headers['content-type']=MEDIA_TYPES[path]
        response.headers.update(HEADERS)
        return response

class Welcome:
    def __init__(self,proxy_file):
        self.root=Path(proxy_file).resolve().parent/'_liliuxflow_welcome'
        self.index=self.root/'index.html'
        self.enabled=not self.root.is_symlink() and safe_file(self.index,self.root) is not None
    def docs_url(self,original):return '/api-docs' if self.enabled and original=='/' else original
    def install(self,app):
        if not self.enabled:return
        app.mount('/liliuxflow-welcome',WelcomeStatic(directory=self.root,html=False,follow_symlink=False),name='liliuxflow-welcome')
        @app.api_route('/',methods=['GET','HEAD'],include_in_schema=False)
        async def welcome_root():
            info=safe_file(self.index,self.root)
            if info is None:return Response('Welcome unavailable',status_code=503,headers=HEADERS)
            return WelcomeFileResponse(self.index,stat_result=info,media_type='text/html',headers=HEADERS)
