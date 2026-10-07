#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Build Welcome assets with a nonvisual, edge-only backend-guard marker."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tempfile

ROOT = Path(__file__).resolve().parents[2]
ASSETS = frozenset({
    'index.html', 'welcome.css', 'welcome.js', 'favicon.svg',
    'welcome-polish.css', 'welcome-polish.js',
    'fonts/InstrumentSans-latin-variable.woff2', 'fonts/OFL.txt',
    'fonts/provenance.json',
})
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; "
       "img-src 'self' data:; font-src 'self'; connect-src 'self'; "
       "object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
HEADERS = {
    'Content-Security-Policy': CSP,
    'Referrer-Policy': 'no-referrer',
    'X-Frame-Options': 'DENY',
    'X-Content-Type-Options': 'nosniff',
    'Cache-Control': 'no-store',
    'Permissions-Policy': 'camera=(), microphone=(), geolocation=()',
}
EDGE_GUARD_ATTRIBUTE = b' data-edge-backend-guard="true"'


def edge_html(source_html):
    # The authoritative HTML stays unchanged on disk. Alter only this one
    # nonvisual attribute in generated HTML, keeping every visible byte intact.
    if source_html.count(b'<body ') != 1 or b'data-edge-backend-guard' in source_html:
        raise ValueError('Exact unmarked Welcome body is required')
    return source_html.replace(b'<body ', b'<body' + EDGE_GUARD_ATTRIBUTE + b' ', 1)


def source_inventory(source):
    """Reject unexpected files and links; copy only a bounded source tree."""
    source = Path(source).absolute()
    if source.resolve() != source or any(path.is_symlink() for path in (source, *source.parents)):
        raise ValueError('Welcome source must be a canonical directory without links')
    if not source.is_dir():
        raise ValueError('Welcome source directory does not exist')
    files = {}
    for path in sorted(source.rglob('*')):
        if path.is_symlink():
            raise ValueError('Asset links are refused')
        name = path.relative_to(source).as_posix()
        if path.is_dir():
            if name != 'fonts':
                raise ValueError('Unexpected source directory')
            continue
        if not path.is_file() or name not in ASSETS:
            raise ValueError('Unexpected source asset')
        data = path.read_bytes()
        if len(data) > 4 * 1024**2:
            raise ValueError('Asset exceeds the bounded build limit')
        files[name] = data
    if set(files) != ASSETS or sum(map(len, files.values())) > 16 * 1024**2:
        raise ValueError('The exact nine bounded Welcome assets are required')
    return files


def output_inventory(output):
    files = {}
    for path in sorted(output.rglob('*')):
        if path.is_symlink():
            raise ValueError('Generated output links are refused')
        if path.is_file():
            files[path.relative_to(output).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
        elif path.is_dir():
            if path.relative_to(output).as_posix() not in {'liliuxflow-welcome', 'liliuxflow-welcome/fonts'}:
                raise ValueError('Unexpected generated output directory')
        else:
            raise ValueError('Generated output must contain regular files only')
    return files


def build(source, output, replace=False):
    files = source_inventory(source)
    generated_html = edge_html(files['index.html'])
    source = Path(source).absolute()
    output = Path(output).absolute()
    if (output.resolve() != output or any(path.is_symlink() for path in (output, *output.parents))
            or output == source or output.is_relative_to(source)):
        raise ValueError('Build output must be canonical and outside the source')
    marker = output.parent / f'.{output.name}.edge-build.json'
    if output.exists():
        if not replace or not output.is_dir() or marker.is_symlink() or not marker.is_file():
            raise ValueError('Existing output requires --replace and a verified builder ownership receipt')
        previous = json.loads(marker.read_text())
        if previous.get('builder') != 'liliuxflow-edge-static-v1' or previous.get('files') != output_inventory(output):
            raise ValueError('Existing output changed; replacement refused')
    elif marker.exists():
        raise ValueError('Stale output receipt requires a new build directory')
    output.parent.mkdir(parents=True, exist_ok=True)
    staged = Path(tempfile.mkdtemp(prefix='.edge-static-', dir=output.parent))
    old = None
    try:
        (staged / 'index.html').write_bytes(generated_html)
        for name, data in files.items():
            destination = staged / 'liliuxflow-welcome' / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(generated_html if name == 'index.html' else data)
        # Scope the only CSP change to edge Welcome resources. It permits the
        # same-origin, user-triggered readiness probe; backend routes stay out.
        sections = []
        for route in ('/', '/index.html', '/liliuxflow-welcome/*'):
            sections.append(route + '\n' + ''.join(f'  {name}: {value}\n' for name, value in HEADERS.items()))
        (staged / '_headers').write_text('\n'.join(sections), encoding='utf-8')
        ownership = {'builder': 'liliuxflow-edge-static-v1', 'files': output_inventory(staged)}
        if output.exists():
            # Re-check immediately before replacement and only replace output
            # whose entire file inventory matches this builder's last receipt.
            if previous['files'] != output_inventory(output):
                raise ValueError('Existing output changed during build')
            old = Path(tempfile.mkdtemp(prefix='.edge-static-old-', dir=output.parent))
            old.rmdir()
            output.rename(old)
        try:
            staged.rename(output)
        except BaseException:
            if old:
                old.rename(output)
                old = None
            raise
        marker.write_text(json.dumps(ownership, indent=2) + '\n', encoding='utf-8')
        if old:
            shutil.rmtree(old)
    finally:
        if staged.exists():
            shutil.rmtree(staged)
    return {
        'source': str(source), 'output': str(output), 'source_asset_count': len(files),
        'assets': [
            {'path': name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
            for name, data in sorted(files.items())
        ],
        'html_and_style_transformed': True,
        'html_transformation': 'Add only data-edge-backend-guard="true" to the existing body element',
        'css_transformed': False,
        'visual_design_transformed': False,
        'generated_html_sha256': hashlib.sha256(generated_html).hexdigest(),
        'csp_change': "Welcome connect-src 'none' to 'self' for the on-demand probe",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT / 'assets/welcome')
    parser.add_argument('--out', type=Path, default=ROOT / 'services/cloudflare-edge/public')
    parser.add_argument('--manifest', type=Path, help='Optional non-public build receipt path')
    parser.add_argument('--replace', action='store_true', help='Replace only unchanged, previously generated output')
    args = parser.parse_args()
    if args.manifest and args.manifest.exists():
        raise ValueError('Build receipt already exists')
    if args.manifest and args.manifest.absolute().is_relative_to(args.out.absolute()):
        raise ValueError('Build receipt must stay outside public assets')
    receipt = build(args.source, args.out, args.replace)
    if args.manifest:
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        args.manifest.write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(receipt, indent=2))


if __name__ == '__main__':
    main()
