# SPDX-License-Identifier: Apache-2.0
"""Install the reviewed static Welcome into a new isolated native runtime."""
import ast
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
from common import DistributionError
BEFORE_SHA='8e3a49e253c6ae0a8fc3bb5ceb0c6d69395a9fe8b87575e3ad051d8bf05dbbe7'


PACKAGE='_liliuxflow_welcome'


HELPER='_liliuxflow_welcome_runtime.py'


ASSETS={'index.html','welcome.css','welcome.js','favicon.svg',
        'welcome-polish.css','welcome-polish.js',
        'fonts/InstrumentSans-latin-variable.woff2','fonts/OFL.txt','fonts/provenance.json'}


BEGIN='# LILIUXFLOW_WELCOME_V1_BEGIN'


END='# LILIUXFLOW_WELCOME_V1_END'


def sha(data):return hashlib.sha256(data).hexdigest()


def canonical(path,*,file=False):
    if not path.is_absolute() or path.resolve()!=path or any(p.is_symlink() for p in (path,*path.parents)):raise ValueError('canonical path without symlink traversal required')
    if file and (not path.is_file() or path.stat().st_uid!=os.getuid() or not stat.S_ISREG(path.stat().st_mode) or path.stat().st_nlink!=1):raise ValueError('owned regular single-link file required')
    return path


def atomic(path,data,mode):
    fd,name=tempfile.mkstemp(prefix='.welcome-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
        os.chmod(name,mode);os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)


def write_new(path,data,mode=0o600):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),mode)
    with os.fdopen(fd,'wb') as stream:
        os.fchmod(stream.fileno(),mode);stream.write(data);stream.flush();os.fsync(stream.fileno())


def patch_source(data):
    text=data.decode();tree=ast.parse(text)
    apps=[node for node in tree.body if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='app' for t in node.targets) and isinstance(node.value,ast.Call) and isinstance(node.value.func,ast.Name) and node.value.func.id=='FastAPI']
    if len(apps)!=1 or BEGIN in text or END in text:raise ValueError('exact unpatched FastAPI app construction required')
    node=apps[0];lines=text.splitlines(True);block=''.join(lines[node.lineno-1:node.end_lineno])
    if block.count('docs_url=_get_docs_url(),')!=1:raise ValueError('original docs URL anchor differs')
    block=block.replace('docs_url=_get_docs_url(),','docs_url=_liliuxflow_welcome.docs_url(_get_docs_url()),')
    before=BEGIN+'\nfrom litellm.proxy._liliuxflow_welcome_runtime import Welcome as _LiliuxflowWelcome\n_liliuxflow_welcome = _LiliuxflowWelcome(__file__)\n'+END+'\n'
    after='\n'+BEGIN+'\n_liliuxflow_welcome.install(app)\n'+END+'\n'
    result=''.join(lines[:node.lineno-1])+before+block+after+''.join(lines[node.end_lineno:])
    ast.parse(result);return result.encode()


def assets_inventory(directory):
    canonical(directory)
    if not directory.is_dir():raise ValueError('frontend asset directory required')
    rows=[];total=0
    for path in sorted(directory.rglob('*')):
        if path.is_symlink():raise ValueError('asset symlink refused')
        name=path.relative_to(directory).as_posix()
        if path.is_dir():
            if name!='fonts':raise ValueError('only explicit welcome asset directories are allowed')
            continue
        if name not in ASSETS:raise ValueError('only the nine explicit welcome assets are allowed')
        canonical(path,file=True);data=path.read_bytes();total+=len(data)
        if len(rows)>=64 or len(data)>4*1024**2 or total>16*1024**2:raise ValueError('bounded static assets required')
        rows.append({'path':name,'sha256':sha(data),'bytes':len(data)})
    if not any(row['path']=='index.html' for row in rows):raise ValueError('static index.html required')
    # External script/style files are required by the route CSP. Never add
    # unsafe-inline or loosen connect-src to compensate for frontend code.
    from html.parser import HTMLParser
    class Check(HTMLParser):
        def handle_starttag(self,tag,attrs):
            values=dict(attrs)
            if any(name.lower().startswith('on') or name.lower()=='style' for name,_ in attrs):raise ValueError('inline event/style attributes refused by CSP')
            if tag=='style' or tag=='script' and not values.get('src'):raise ValueError('inline script/style refused by CSP')
            for name in ('src','href'):
                value=values.get(name,'')
                if tag in ('script','link') and value and not value.startswith('/liliuxflow-welcome/'):raise ValueError('script/style must use same-origin welcome assets')
    Check().feed((directory/'index.html').read_text())
    return rows


def install_welcome(source_root, data_root):
    """New runtime only: preserve API/auth code and the reviewed static route rule."""
    from profile_registry import load_registry
    from welcome_profiles import render_html
    source_root=canonical(Path(source_root));data_root=canonical(Path(data_root))
    proxy=canonical(data_root/'runtime/litellm/lib/python3.12/site-packages/litellm/proxy/proxy_server.py',file=True)
    runtime=proxy.parent/HELPER;target=proxy.parent/PACKAGE
    if runtime.exists() or target.exists() or (data_root/'runtime/release-trust.json').exists() or (data_root/'run/agent.json').exists():
        raise DistributionError('Welcome installation requires a new unstarted runtime')
    before=proxy.read_bytes()
    if sha(before)!=BEFORE_SHA:raise DistributionError('Welcome requires the pinned pristine LiteLLM proxy source')
    after=patch_source(before)
    if sha(after)!='0da5e7bedc0e79095f3a79bc958c65e6f7523b2a206693da03222b2b5e32c6d8':
        raise DistributionError('Welcome source patch differs from the reviewed route injection')
    manifest=json.loads(canonical(source_root/'manifests/distribution/welcome-assets.json',file=True).read_text())
    assets=canonical(source_root/'assets/welcome');rows=assets_inventory(assets)
    expected=[{'path':item['path'].removeprefix('assets/welcome/'),'sha256':item['sha256'],'bytes':item['bytes']} for item in manifest['assets']]
    if rows!=expected or {row['path'] for row in rows}!=ASSETS:raise DistributionError('Welcome source asset manifest differs')
    helper_path=canonical(source_root/'patches/litellm-welcome/runtime.py',file=True);helper=helper_path.read_bytes()
    if manifest['helper']['path']!='patches/litellm-welcome/runtime.py' or sha(helper)!=manifest['helper']['sha256']:
        raise DistributionError('Welcome runtime helper source differs')
    staged=Path(tempfile.mkdtemp(prefix='.welcome-native-',dir=proxy.parent));installed=[]
    try:
        for row in rows:
            value=canonical(assets/row['path'],file=True).read_bytes()
            if sha(value)!=row['sha256']:raise DistributionError('Welcome source asset changed during staging')
            if row['path']=='index.html':value=render_html(value.decode(),load_registry(source_root))[0].encode()
            dest=staged/row['path'];dest.parent.mkdir(parents=True,exist_ok=True);write_new(dest,value,0o644)
            installed.append({'path':str((target/row['path']).relative_to(data_root)),'sha256':sha(value)})
        for folder in staged.rglob('*'):
            if folder.is_dir():folder.chmod(0o755)
        staged.chmod(0o755)
        if proxy.read_bytes()!=before:raise DistributionError('LiteLLM source changed before Welcome installation')
        os.replace(staged,target);atomic(runtime,helper,0o644);atomic(proxy,after,stat.S_IMODE(proxy.stat().st_mode))
    except BaseException:
        if proxy.read_bytes()==after:atomic(proxy,before,stat.S_IMODE(proxy.stat().st_mode))
        if runtime.exists() and runtime.read_bytes()==helper:runtime.unlink()
        if target.exists() and all((data_root/row['path']).is_file() and sha((data_root/row['path']).read_bytes())==row['sha256'] for row in installed):shutil.rmtree(target)
        if staged.exists():shutil.rmtree(staged)
        raise
    return {'helper':{'path':str(runtime.relative_to(data_root)),'sha256':sha(helper)},'assets':installed,
            'proxy_before_sha256':sha(before),'proxy_after_sha256':sha(after)}
