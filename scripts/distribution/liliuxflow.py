#!/usr/bin/env python3
"""Native LiliuxFlow installation entry point. Bootstrap uses Python stdlib only."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import time
import uuid
from common import DistributionError, no_symlinks, private_directory, read_object, relative_path, write_json_new

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PORTS = {'litellm': 4000, 'compat': 8001, 'guard': 8080, 'manager': 8081, 'postgresql': 15432}

def emit(value):
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))

def validate_ports(ports):
    if not isinstance(ports, dict) or set(ports) != set(DEFAULT_PORTS):
        raise DistributionError('five named ports are required')
    if any(type(p) is not int or not 1024 <= p <= 65535 for p in ports.values()) or len(set(ports.values())) != 5:
        raise DistributionError('ports must be distinct integers between 1024 and 65535')
    return ports

def checkpoint_metadata(model_dir):
    """Only metadata and index: no tensor payload, download, mutation, or load."""
    model_dir = no_symlinks(model_dir)
    if model_dir.name.endswith('.liliuxflow-partial') or (model_dir / '.liliuxflow-partial.json').exists():
        raise DistributionError('checkpoint download is still partial; finish models install before setup')
    config = read_object(model_dir / 'config.json')
    lily = config.get('lily', {})
    if config.get('model_type') != 'qwen4_exp' or lily.get('format') != 'qwen4_exp-affine-v1':
        raise DistributionError('external model is not the selected Lily checkpoint format')
    quant = lily.get('quantization', {}).get('default', {})
    if quant.get('bits') != 4 or quant.get('mode') != 'affine' or quant.get('group_size') != 64:
        raise DistributionError('external model does not declare the selected Lily Q4 quantization')
    for name in ('tokenizer.json', 'tokenizer_config.json', 'generation_config.json', 'chat_template.jinja', 'LICENSE'):
        file = no_symlinks(model_dir / name)
        if not file.is_file():
            raise DistributionError('external checkpoint metadata is incomplete')
    index = read_object(model_dir / 'model.safetensors.index.json')
    weight_map = index.get('weight_map')
    if not isinstance(weight_map, dict) or not weight_map:
        raise DistributionError('external checkpoint index has no tensor map')
    shards = set(weight_map.values())
    for name in shards:
        relative_path(name)
        file = no_symlinks(model_dir / name)
        if not name.endswith('.safetensors') or not file.is_file() or file.stat().st_size < 8:
            raise DistributionError('external checkpoint shard is missing or invalid')
    return {'format': lily['format'], 'quantization_bits': 4, 'shard_count': len(shards),
            'config_sha256': hashlib.sha256((model_dir / 'config.json').read_bytes()).hexdigest(),
            'index_sha256': hashlib.sha256((model_dir / 'model.safetensors.index.json').read_bytes()).hexdigest(),
            'revision_verified': False, 'tensor_payload_verified': False,
            'validation': 'metadata_only; release checksum verification still required'}

def load_install(data_root):
    data_root = no_symlinks(data_root)
    if not data_root.is_dir():
        raise DistributionError('installation is not configured')
    private_directory(data_root)
    from trust import installation
    config = installation(data_root)
    validate_ports(config.get('ports'))
    if config.get('data_root') != str(data_root) or config.get('owner_uid') != os.getuid():
        raise DistributionError('installation path or owner differs; explicit migration is required')
    try:
        uuid.UUID(config['installation_id'])
    except (KeyError, ValueError, TypeError):
        raise DistributionError('installation identity is invalid')
    return config

def setup(args):
    data_root = no_symlinks(args.data_root)
    if data_root == ROOT or ROOT in data_root.parents or data_root in ROOT.parents:
        raise DistributionError('portable data directory must be separate from source tree')
    without_model = getattr(args, 'without_model', False)
    if without_model == (args.model_dir is not None):
        raise DistributionError('choose exactly one of --model-dir or --without-model')
    model_path = no_symlinks(args.model_dir) if args.model_dir is not None else None
    if model_path is not None and (data_root == model_path or data_root in model_path.parents or model_path in data_root.parents):
        raise DistributionError('private data and read-only weights must not overlap')
    ports = validate_ports(dict(DEFAULT_PORTS, **dict(args.port or [])))
    metadata = checkpoint_metadata(model_path) if model_path is not None else None
    if data_root.exists():
        config = load_install(data_root)
        if config['model_dir'] != (str(model_path) if model_path is not None else None) or config['ports'] != ports:
            raise DistributionError('existing installation differs; refusing to overwrite identity or settings')
        (data_root / 'uninstalled.json').unlink(missing_ok=True)
        emit({'state': 'already_configured', 'installation_id': config['installation_id'], 'credentials_preserved': True})
        return 0
    data_root = private_directory(data_root)
    for sub in ('secrets', 'logs', 'run', 'cache', 'backups'):
        private_directory(data_root / sub)
    identity = str(uuid.uuid4())
    config = {'schema_version': 2, 'installation_id': identity, 'owner_uid': os.getuid(),
              'data_root': str(data_root), 'source_root': str(ROOT), 'model_dir': str(model_path) if model_path is not None else None,
              'model_state': 'attached' if model_path is not None else 'not_configured',
              'model_id': 'qwen3.8-flash-next-lily-q4' if model_path is not None else None,
              'profile': 'safe64k', 'bind_host': '127.0.0.1', 'ports': ports, 'checkpoint': metadata,
              'launchd_label': 'com.diurnoctra.liliuxflow.' + identity,
              'runtime_state': 'NOT_BUILT', 'database_initialized': False, 'caller_key_created': False}
    write_json_new(data_root / 'secrets' / 'bootstrap.json', {
        'LITELLM_MASTER_KEY': 'sk-' + secrets.token_urlsafe(36), 'LITELLM_SALT_KEY': secrets.token_urlsafe(36),
        'DB_PASSWORD': secrets.token_urlsafe(36), 'PG_OWNER_PASSWORD': secrets.token_urlsafe(36), 'UI_USERNAME': 'admin', 'UI_PASSWORD': secrets.token_urlsafe(24),
        'MANAGER_BACKEND_TOKEN': secrets.token_urlsafe(36), 'GUARD_CONTROL_TOKEN': secrets.token_urlsafe(36)})
    write_json_new(data_root / 'install.json', config)
    emit({'state': 'configured', 'installation_id': identity, 'credentials': 'private bootstrap.json; values never printed',
          'runtime': 'NOT_BUILT', 'database_initialized': False, 'model_validation': metadata,
          'model_configured': model_path is not None, 'inference_ready': False})
    return 0

def listening(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.15)
        return sock.connect_ex(('127.0.0.1', port)) == 0

def host_metadata():
    result = {'os': platform.system(), 'os_version': platform.mac_ver()[0], 'machine': platform.machine(), 'ram_bytes': None, 'gpu': None}
    if platform.system() == 'Darwin':
        ram = subprocess.run(['/usr/sbin/sysctl', '-n', 'hw.memsize'], capture_output=True, text=True)
        if ram.returncode == 0:
            result['ram_bytes'] = int(ram.stdout.strip())
        # Only hardware metadata. No environment, process arguments, keys, or system profile dump.
        chip = subprocess.run(['/usr/sbin/sysctl', '-n', 'machdep.cpu.brand_string'], capture_output=True, text=True)
        if chip.returncode == 0:
            result['chip'] = chip.stdout.strip()
    return result

def doctor(args):
    config = load_install(args.data_root)
    host = host_metadata()
    problems = []
    warnings = []
    guard_state = None
    degraded = False
    if host['os'] != 'Darwin' or host['machine'] != 'arm64':
        problems.append('native serving requires Darwin arm64; CPU package checks are portable')
    hardware_baseline_matches = host.get('chip') == 'Apple M5 Max' and host['ram_bytes'] == 128 * 1024**3
    if not hardware_baseline_matches:
        warnings.append('hardware differs from the accepted M5 Max / 128 GiB baseline; compatibility is unverified')
    free = shutil.disk_usage(args.data_root).free
    if free < 64 * 1024**3:
        problems.append('free disk is below the 64 GiB build floor')
    from trust import model_configured
    configured = model_configured(config)
    model = None
    if configured:
        try:
            model = checkpoint_metadata(Path(config['model_dir']))
            if model['config_sha256'] != config['checkpoint']['config_sha256'] or model['index_sha256'] != config['checkpoint']['index_sha256']:
                problems.append('external checkpoint metadata changed')
        except (DistributionError, OSError):
            problems.append('external checkpoint validation failed')
    occupied = {name: listening(port) for name, port in config['ports'].items()}
    if any(occupied.values()):
        from ownership import unchanged
        registry = Path(args.data_root) / 'run/agent.json'
        if not registry.is_file() or not unchanged(read_object(registry)['agent']):
            problems.append('configured ports have listeners; ownership must be established before start')
    tools = {name: shutil.which(name) is not None for name in ('uv', 'cargo', 'psql', 'initdb', 'pg_ctl')}
    if config['runtime_state'] == 'NOT_BUILT' and not all(tools.values()):
        problems.append('one or more native runtime toolchains are unavailable on PATH')
    if config['runtime_state'] == 'NOT_BUILT':
        problems.append('portable runtime has not been built and verified')
    else:
        from trust import validate
        try:
            trusted=validate(no_symlinks(args.data_root), require_checkpoint='controlplane' if getattr(args,'controlplane_only',False) else 'available')
            # Read-only health classification, never cleanup or model mutation.
            if occupied.get('guard') and not problems:
                from agent import guard_admission_status
                guard_state=guard_admission_status(trusted)
                degraded=guard_state['admission_paused']
        except (DistributionError, OSError, ValueError, KeyError, TypeError):
            problems.append('release trust or owned guard admission verification failed')
    inference_ready = configured and not getattr(args,'controlplane_only',False) and not problems and not degraded
    emit({'state': 'blocked' if problems else 'DEGRADED' if degraded else 'ready' if inference_ready else 'controlplane_ready',
          'serving_ready': inference_ready, 'inference_ready': inference_ready, 'controlplane_ready': not problems and not degraded,
          'model_configured': configured,
          'next_step': None if configured else 'models attach qwen3.8-flash-next-lily-q4 --model-dir PATH',
          'guard_admission_paused':guard_state.get('admission_paused') if guard_state else None,
          'guard_pause_reason':guard_state.get('pause_reason') if guard_state else None,
          'payload_verified':False if not configured or getattr(args,'controlplane_only',False) else None, 'host': host, 'free_disk_bytes': free,
          'tools': tools, 'occupied_ports': occupied, 'model': model, 'problems': problems,
          'hardware_baseline_policy': 'advisory', 'hardware_baseline_matches': hardware_baseline_matches,
          'warnings': warnings})
    return 2 if problems or degraded else 0

def status(args):
    config = load_install(args.data_root)
    from trust import model_configured, private_file
    configured = model_configured(config)
    control_ready = False
    inference_ready = False
    registry = Path(args.data_root) / 'run/agent.json'
    if registry.is_file():
        from ownership import unchanged
        record = read_object(private_file(registry))
        control_ready = record.get('installation_id') == config['installation_id'] and record.get('ready') is True and unchanged(record['agent'])
        inference_ready = control_ready and configured and record.get('controlplane_only') is not True
    emit({'installation_id': config['installation_id'], 'runtime_state': config['runtime_state'],
          'database_initialized': config['database_initialized'], 'caller_key_created': config['caller_key_created'],
          'ports_listening': {name: listening(port) for name, port in config['ports'].items()},
          'owned_processes': 'registered' if control_ready else 'not_ready', 'model_resident': None,
          'model_configured': configured, 'controlplane_ready': control_ready, 'inference_ready': inference_ready})
    return 0

def require_runtime(args):
    config = load_install(args.data_root)
    if config['runtime_state'] == 'NOT_BUILT':
        raise DistributionError('portable runtime adapter is not built; run build first')
    from agent import start, stop
    operation = start if args.command == 'start' else stop
    options={'dry_run':args.dry_run,'controlplane_only':args.controlplane_only}
    if args.command=='stop':options['force']=getattr(args,'force',False)
    emit(operation(no_symlinks(args.data_root),**options))
    return 0

def configure_runtime(args):
    config = load_install(args.data_root)
    if config['database_initialized'] or (Path(args.data_root) / 'run/agent.json').exists():
        raise DistributionError('port configuration requires this installation to be uninitialized and stopped')
    ports = validate_ports(dict(config['ports'], **dict(args.port)))
    from trust import atomic_private_json
    config['ports'] = ports
    atomic_private_json(Path(args.data_root) / 'install.json', config)
    emit({'state':'configured','ports':ports,'credentials_preserved':True,'installation_id':config['installation_id']})
    return 0

def build_runtime(args):
    from build_native import build
    emit(build(no_symlinks(args.data_root), cargo=args.cargo, go=args.go, node=args.node,
               npm_cli=args.npm_cli, pg_bin=args.pg_bin, ui_manifest=args.ui_manifest, execute=args.execute))
    return 0

def verify_model(args):
    from trust import verify_checkpoint, verify_metadata
    config = load_install(args.data_root)
    from trust import model_configured
    if not model_configured(config):
        raise DistributionError('model is not configured; use models attach with a local checkpoint')
    private_directory(args.data_root / 'runtime')
    emit((verify_metadata if args.metadata_only else verify_checkpoint)(no_symlinks(args.data_root)))
    return 0


def models(args):
    from model_catalog import HUB_VERSION, HELPER_PROJECT, load_catalog, select_model
    if args.model_command == 'list':
        emit({'models': [model.public_info() for model in load_catalog(ROOT).values()], 'network_operation': False})
        return 0
    model = select_model(ROOT, args.model_id)
    if args.model_command == 'info':
        emit(model.public_info())
        return 0
    if args.model_command == 'attach':
        from model_installer import attach_model
        emit(attach_model(no_symlinks(args.data_root), args.model_id, args.model_dir))
        return 0
    token_name = model_token_environment_name(args.token_env)
    from model_installer import model_destination
    output = model_destination(args.output, ROOT, args.data_root)
    uv = shutil.which('uv')
    if uv is None:
        raise DistributionError('uv is required for the isolated locked optional model helper')
    helper = private_directory(Path.home() / 'Library/Caches/LiliuxFlow/model-installer' / HUB_VERSION)
    env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'HOME': str(Path.home()), 'LANG': 'en_US.UTF-8',
           'UV_PROJECT_ENVIRONMENT': str(helper / 'venv'), 'UV_CACHE_DIR': str(private_directory(helper / 'uv-cache')),
           'HF_HUB_DISABLE_IMPLICIT_TOKEN': '1', 'HF_HUB_DISABLE_XET': '1', 'HF_HUB_DISABLE_TELEMETRY': '1',
           'HF_HUB_DISABLE_PROGRESS_BARS': '1', 'HF_HUB_VERBOSITY': 'error', 'HF_HOME': str(helper / 'hf-metadata')}
    if token_name:
        token = os.environ.get(token_name)
        if not token:
            raise DistributionError('explicit Hugging Face token environment variable is empty')
        env['LILIUXFLOW_MODEL_INSTALL_TOKEN'] = token
    if args.execute:
        print('Optional model: ' + model.entry['repository'] + ' @ ' + model.entry['revision']
              + '; final payload ' + str(round(model.total_bytes / 1024**3, 2)) + ' GiB. Review '
              + model.entry['license_name'] + ': ' + model.entry['homepage'] + '/blob/' + model.entry['revision']
              + '/LICENSE. Free disk and reusable partial data are checked before downloading tensors.', file=sys.stderr, flush=True)
    argv = [uv, 'run', '--locked', '--no-dev', '--project', str(ROOT / HELPER_PROJECT), 'python',
            str(ROOT / 'scripts/distribution/model_installer.py'), '--source-root', str(ROOT),
            '--data-root', str(no_symlinks(args.data_root)), '--model-id', args.model_id, '--output', str(output),
            '--execute' if args.execute else '--dry-run']
    # SDK output and exception details are sanitized by the helper. uv diagnostics
    # are kept off the public result and cannot print inherited application tokens.
    result = run_model_helper(argv, env)
    try:
        value = json.loads(result.stdout)
        if not isinstance(value, dict):
            raise ValueError()
    except ValueError:
        raise DistributionError('optional model helper failed; verify uv and its locked dependencies')
    emit(value)
    return result.returncode


def model_token_environment_name(value):
    """Only an explicit HF credential name may be forwarded to the downloader."""
    if value is None:
        return None
    import re
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,127}', value):
        raise DistributionError('HF token environment variable name must be a bounded identifier')
    name = value.upper()
    app_credentials = {'LITELLM_MASTER_KEY', 'LITELLM_SALT_KEY', 'LITELLM_VIRTUAL_KEY', 'UI_PASSWORD',
                       'DB_PASSWORD', 'PG_OWNER_PASSWORD', 'MANAGER_BACKEND_TOKEN', 'GUARD_CONTROL_TOKEN',
                       'GITHUB_TOKEN', 'GH_TOKEN', 'CLOUDFLARE_API_TOKEN', 'OPENAI_API_KEY'}
    if name in app_credentials or name.startswith(('LITELLM_', 'RECOVERY_', 'LILIUXFLOW_')):
        raise DistributionError('application credentials cannot be used as a Hugging Face token; provide an HF-specific variable')
    return value


def run_model_helper(argv, env, *, cancellation_grace_seconds=15):
    """Own the uv/helper subtree and bound cancellation without group signals."""
    from ownership import capture, descendants, unchanged
    if type(cancellation_grace_seconds) not in (int, float) or not 0 <= cancellation_grace_seconds <= 15:
        raise DistributionError('model helper cancellation bound is invalid')
    cancelled = None
    process = None
    root = None
    known = {}
    previous = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM)}

    def request_cancel(number, _frame):
        nonlocal cancelled
        if cancelled is None:
            cancelled = number

    def observe():
        nonlocal root
        if process.poll() is not None:
            return
        try:
            current = capture(process.pid, parent=os.getpid())
        except DistributionError:
            if process.poll() is not None:
                return  # Normal completion can race the read-only PID snapshot.
            raise
        if root is not None and any(current[k] != root[k] for k in ('pid', 'uid', 'started')):
            raise DistributionError('owned model helper identity changed; no unrelated process was signalled')
        # uv can exec its interpreter. The unreaped Popen child, UID, start time
        # and direct parent keep that transition within this exact launch.
        root = current
        try:
            children = descendants(process.pid)
        except (DistributionError, ProcessLookupError):
            children = []  # A short-lived child may exit during a snapshot.
        for child in children:
            known[child['pid']] = child

    def send(identity, number):
        if identity['pid'] in (os.getpid(), os.getppid()) or identity['uid'] != os.getuid():
            raise DistributionError('model helper cancellation ownership differs')
        if unchanged(identity):
            try:
                os.kill(identity['pid'], number)
            except ProcessLookupError:
                pass

    def cancel_owned():
        if process is None:
            return
        deadline = time.monotonic() + cancellation_grace_seconds
        initial_signal = cancelled if cancelled in (signal.SIGINT, signal.SIGTERM) else signal.SIGINT
        signalled = set()
        while True:
            observe()
            # Signal the SDK child before uv can exit and reparent it. Saved
            # fingerprints still identify it after its original parent exits.
            for identity in reversed(tuple(known.values())):
                key = (identity['pid'], identity['started'], identity['command_sha256'])
                if key not in signalled:
                    send(identity, initial_signal);signalled.add(key)
            if root is not None:
                key = (root['pid'], root['started'], root['command_sha256'])
                if key not in signalled:
                    send(root, initial_signal);signalled.add(key)
            alive = any(unchanged(identity) for identity in known.values())
            if process.poll() is not None and not alive:
                break
            if time.monotonic() >= deadline:
                break
            try:
                process.communicate(timeout=min(0.1, max(0.01, deadline - time.monotonic())))
            except subprocess.TimeoutExpired:
                pass
        # Allow the SDK's normal request timeout and atomic metadata cleanup
        # before escalation. SIGKILL is a last resort for nonresponsive exact
        # owned fingerprints; incomplete SDK files remain in staging.
        observe()
        for identity in reversed(tuple(known.values())):
            send(identity, signal.SIGKILL)
        if root is not None:
            send(root, signal.SIGKILL)
        try:
            process.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            raise DistributionError('owned model helper did not exit within the cancellation bound; inspect its retained partial')
        deadline = time.monotonic() + 1
        while any(unchanged(identity) for identity in known.values()) and time.monotonic() < deadline:
            time.sleep(0.05)
        if any(unchanged(identity) for identity in known.values()):
            raise DistributionError('owned SDK child remains after cancellation; inspect its retained partial')

    try:
        for number in previous:
            signal.signal(number, request_cancel)
        # A separate session stops terminal Ctrl-C from racing our child-tree
        # audit. Signals are forwarded to verified individual PIDs only.
        process = subprocess.Popen(argv, env=env, text=True, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        while True:
            observe()
            if cancelled is not None:
                raise KeyboardInterrupt()
            try:
                stdout, stderr = process.communicate(timeout=0.1)
                if cancelled is not None:
                    raise KeyboardInterrupt()
                return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
            except subprocess.TimeoutExpired:
                continue
    except KeyboardInterrupt:
        cancel_owned()
        raise
    except BaseException:
        cancel_owned()
        raise
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)
        if process is not None:
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    stream.close()

def initialize_runtime(args):
    if not args.execute:
        emit({'state': 'initialization_plan', 'new_database_only': True, 'execute_required': True})
        return 0
    from agent import initialize
    emit(initialize(no_symlinks(args.data_root), controlplane_only=args.controlplane_only))
    return 0

def restore_runtime(args):
    from backup_native import restore
    if not args.execute:
        emit({'state': 'restore_plan', 'target': 'new empty own installation', 'execute_required': True})
        return 0
    emit(restore(no_symlinks(args.data_root), no_symlinks(args.archive)))
    return 0

def rollback_runtime(args):
    from trust import validate
    from agent import start, stop
    current = no_symlinks(args.data_root)
    previous = no_symlinks(args.previous_data_root)
    if current == previous:
        raise DistributionError('rollback needs an existing previous installation')
    mode='controlplane' if args.controlplane_only else 'available'
    old = validate(previous,require_checkpoint=mode);new = validate(current,require_checkpoint=mode)
    if old['config']['owner_uid'] != new['config']['owner_uid'] or old['config']['ports'] != new['config']['ports']:
        raise DistributionError('rollback installation owner or ports differ')
    if not args.execute:
        emit({'state':'rollback_plan','data_preserved':True,'execute_required':True})
        return 0
    stop(current)
    emit(start(previous,controlplane_only=args.controlplane_only))
    return 0

def backup(args):
    config = load_install(args.data_root)
    if config['database_initialized']:
        from backup_native import backup as native_backup
        emit(native_backup(no_symlinks(args.data_root), no_symlinks(args.output)))
        return 0
    destination = no_symlinks(args.output)
    if destination.exists():
        raise DistributionError('backup output already exists')
    if not destination.parent.is_dir():
        raise DistributionError('backup parent must already exist')
    private_directory(destination.parent)
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, 'wb') as handle, tarfile.open(fileobj=handle, mode='w:gz') as archive:
            for file in [Path(args.data_root) / 'install.json', *sorted((Path(args.data_root) / 'secrets').iterdir())]:
                no_symlinks(file)
                if not file.is_file():
                    raise DistributionError('backup contains a non-regular credential file')
                info = archive.gettarinfo(str(file), arcname=str(file.relative_to(args.data_root)))
                info.uid = info.gid = 0
                info.uname = info.gname = ''
                info.mode = 0o600
                with file.open('rb') as source:
                    archive.addfile(info, source)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    emit({'state': 'backed_up', 'scope': 'installation settings and bootstrap credentials; no initialized database',
          'encrypted': False, 'mode': '0600', 'model_included': False, 'cache_included': False})
    return 0

def uninstall(args):
    config = load_install(args.data_root)
    if config['runtime_state'] != 'NOT_BUILT':
        from agent import stop
        stop(no_symlinks(args.data_root))
    marker = Path(args.data_root) / 'uninstalled.json'
    if not marker.exists():
        write_json_new(marker, {'schema_version': 1, 'installation_id': config['installation_id'], 'data_preserved': True})
    emit({'state': 'uninstalled', 'data_preserved': True, 'model_preserved': True, 'launchd_was_installed': False})
    return 0

def parse_port(value):
    try:
        name, port = value.split('=', 1)
        if name not in DEFAULT_PORTS:
            raise ValueError()
        return name, int(port)
    except ValueError:
        raise argparse.ArgumentTypeError('port must be NAME=INTEGER for a named service')

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', action='version', version='LiliuxFlow 0.1.0')
    parser.add_argument('--data-root', type=Path, default=Path.home() / 'Library/Application Support/LiliuxFlow')
    commands = parser.add_subparsers(dest='command', required=True)
    command = commands.add_parser('setup', help='generate an isolated installation, no DB/service/model load')
    choice = command.add_mutually_exclusive_group(required=True)
    choice.add_argument('--model-dir', type=Path)
    choice.add_argument('--without-model', action='store_true')
    command.add_argument('--port', action='append', type=parse_port)
    model_commands = commands.add_parser('models', help='offline catalog and explicit optional model installation').add_subparsers(dest='model_command', required=True)
    model_commands.add_parser('list')
    model_commands.add_parser('info').add_argument('model_id')
    command = model_commands.add_parser('install', help='network metadata preview or explicit download; never loads a model')
    command.add_argument('model_id')
    command.add_argument('--output', type=Path, required=True)
    choice = command.add_mutually_exclusive_group(required=True)
    choice.add_argument('--dry-run', action='store_true')
    choice.add_argument('--execute', action='store_true')
    command.add_argument('--token-env', help='explicit HF token environment variable name; anonymous by default')
    command = model_commands.add_parser('attach', help='verify and attach existing local weights to a stopped installation')
    command.add_argument('model_id')
    command.add_argument('--model-dir', type=Path, required=True)
    for name in ('status', 'uninstall'):
        commands.add_parser(name)
    commands.add_parser('doctor').add_argument('--controlplane-only',action='store_true')
    commands.add_parser('verify-model').add_argument('--metadata-only', action='store_true')
    for name in ('start', 'stop'):
        command=commands.add_parser(name)
        command.add_argument('--dry-run', action='store_true')
        command.add_argument('--controlplane-only', action='store_true')
        if name=='stop':command.add_argument('--force',action='store_true',help='cancel inference and stop only this verified installation')
    commands.add_parser('configure').add_argument('--port',action='append',type=parse_port,required=True)
    command = commands.add_parser('build')
    command.add_argument('--execute', action='store_true')
    for name in ('cargo','go','node','npm-cli','pg-bin','ui-manifest'):
        command.add_argument('--'+name, type=Path)
    command=commands.add_parser('initialize')
    command.add_argument('--execute', action='store_true')
    command.add_argument('--controlplane-only', action='store_true')
    command = commands.add_parser('restore')
    command.add_argument('--archive',type=Path,required=True)
    command.add_argument('--execute',action='store_true')
    command = commands.add_parser('rollback')
    command.add_argument('--previous-data-root',type=Path,required=True)
    command.add_argument('--controlplane-only',action='store_true')
    command.add_argument('--execute',action='store_true')
    command = commands.add_parser('backup')
    command.add_argument('--output', type=Path, required=True)
    command = commands.add_parser('build-release')
    command.add_argument('--commit', required=True)
    command.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        if args.command == 'build-release':
            from package import build_archive
            emit(build_archive(ROOT, args.commit, args.output))
            return 0
        return {'setup': setup, 'models': models, 'doctor': doctor, 'status': status, 'start': require_runtime, 'stop': require_runtime,
                'backup': backup, 'uninstall': uninstall, 'configure': configure_runtime, 'build': build_runtime, 'initialize': initialize_runtime,
                'verify-model': verify_model, 'restore': restore_runtime, 'rollback': rollback_runtime}[args.command](args)
    except KeyboardInterrupt:
        message = 'operation cancelled'
        if args.command == 'models' and args.model_command == 'install':
            message += '; retry the same explicit model install to resume its owned partial download'
        emit({'state': 'interrupted', 'error': message})
        return 130
    except (DistributionError, OSError, ValueError, KeyError, TypeError) as exc:
        # Generic external I/O exceptions can contain private paths. Never emit their contents.
        emit({'state': 'error', 'error': str(exc) if isinstance(exc, DistributionError) else 'invalid or inaccessible installation data'})
        return 2

if __name__ == '__main__':
    raise SystemExit(main())
