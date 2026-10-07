#!/usr/bin/env python3
"""Verify the selected first-party Apache license and unchanged upstream notices."""
import argparse
import json
from pathlib import Path
from common import DistributionError,read_object,relative_path
from trust import verified_file,sha256,within
ROOT=Path(__file__).resolve().parents[2]
APACHE_LICENSE={
    'path':'LICENSE',
    'sha256':'cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30',
    'bytes':11358,
    'source_url':'https://www.apache.org/licenses/LICENSE-2.0.txt',
}

def verify_first_party(source=ROOT,manifest=None):
    manifest=manifest if manifest is not None else read_object(source/'manifests/distribution/original-notices.json')
    if manifest.get('schema_version')!=1 or manifest.get('first_party_license')!='Apache-2.0':
        raise DistributionError('notice manifest identity or first-party license differs')
    if manifest.get('first_party_license_adopted') is not True or manifest.get('new_third_party_terms_accepted') is not False:
        raise DistributionError('first-party adoption or third-party terms policy differs')
    item=manifest.get('first_party_license_file')
    if not isinstance(item,dict) or any(item.get(k)!=v for k,v in APACHE_LICENSE.items()):
        raise DistributionError('first-party license is not the official Apache-2.0 text')
    license_file=verified_file(source,item)
    if license_file.stat().st_size!=APACHE_LICENSE['bytes']:
        raise DistributionError('first-party license byte count differs')
    notice=manifest.get('first_party_notice_file')
    if not isinstance(notice,dict) or notice.get('path')!='NOTICE':
        raise DistributionError('first-party NOTICE record differs')
    notice_file=verified_file(source,notice)
    if notice_file.stat().st_size!=notice.get('bytes') or notice_file.stat().st_size>16*1024**2:
        raise DistributionError('first-party NOTICE byte count differs')
    upstream=[item for item in manifest.get('files',[]) if isinstance(item,dict) and item.get('component')=='lily' and item.get('upstream_path')=='NOTICE']
    if len(upstream)!=1:
        raise DistributionError('original Lily NOTICE record is missing or duplicate')
    original=verified_file(source,upstream[0])
    if original.stat().st_size!=upstream[0].get('bytes') or original.stat().st_size>16*1024**2 or original.read_bytes() not in notice_file.read_bytes():
        raise DistributionError('first-party NOTICE does not preserve the original Lily attribution')
    return {'first_party_license':'Apache-2.0','first_party_license_adopted':True,'new_third_party_terms_accepted':False}

def refresh_first_party(source=ROOT):
    """Refresh only first-party policy/artifact metadata; retain upstream provenance."""
    path=source/'manifests/distribution/original-notices.json'
    manifest=read_object(path)
    license_file=verified_file(source,APACHE_LICENSE)
    if license_file.stat().st_size!=APACHE_LICENSE['bytes']:
        raise DistributionError('first-party license byte count differs')
    notice_file=within(source,'NOTICE')
    notice={'path':'NOTICE','sha256':sha256(notice_file),'bytes':notice_file.stat().st_size}
    candidate=dict(manifest)
    candidate.update(first_party_license='Apache-2.0',first_party_license_adopted=True,
                     new_third_party_terms_accepted=False,first_party_license_file=dict(APACHE_LICENSE),
                     first_party_notice_file=notice)
    # This former field described third-party term acceptance; the explicit
    # replacement distinguishes it from the owner's first-party license grant.
    candidate.pop('new_terms_accepted',None)
    first_party=verify_first_party(source,candidate)
    path.write_text(json.dumps(candidate,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    return {'status':'REFRESHED_FIRST_PARTY_NOTICE_METADATA',**first_party}

def verify(source=ROOT):
    manifest=read_object(source/'manifests/distribution/original-notices.json')
    first_party=verify_first_party(source,manifest)
    pins=read_object(source/'manifests/distribution/native-sources.json')
    security=read_object(source/'manifests/distribution/security-tools.lock.json')
    seen=set();entries=manifest.get('files',[])
    if not isinstance(entries,list) or not entries:raise DistributionError('original notice inventory is empty')
    for item in entries:
        relative_path(item['upstream_path'])
        if item['path'] in seen:raise DistributionError('duplicate original notice')
        seen.add(item['path']);file=verified_file(source,item)
        identity=item.get('source_identity')
        if file.stat().st_size!=item['bytes'] or not isinstance(identity,dict) or not identity:raise DistributionError('notice byte count or source identity differs')
        component=item['component']
        if component in ('lily','litellm','llama_swap') and any(identity.get(k)!=pins[component][k] for k in ('repository','commit','archive_sha256')):raise DistributionError('notice pinned upstream differs')
        if component in ('gitleaks','syft') and (identity.get('version')!=security[component]['version'] or identity.get('archive_sha256')!=security[component]['archive_sha256']):raise DistributionError('notice scanner/SBOM source pin differs')
        if component=='go' and identity.get('archive_sha256')!=pins['project_toolchains']['go']['sha256']:raise DistributionError('notice Go toolchain source pin differs')
    required={'lily','litellm','llama_swap','i18next','react-i18next','moment','gitleaks','syft','go','actions-checkout','checkpoint'}
    if (source/'assets/welcome/fonts/OFL.txt').exists() or (source/'assets/welcome/fonts/InstrumentSans-latin-variable.woff2').exists():
        required.add('instrument-sans')
    if not required.issubset({item['component'] for item in entries}):raise DistributionError('required component original notice is missing')
    return {'status':'PASS_ORIGINAL_NOTICE_BYTES','notice_files':len(entries),**first_party,'full_runtime_transitive_notice_inventory':'PENDING_FINAL_NATIVE_BUILD'}
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',type=Path,default=ROOT);p.add_argument('--refresh-first-party',action='store_true',help='refresh first-party license/NOTICE metadata while retaining upstream provenance');a=p.parse_args()
    try:print(json.dumps(refresh_first_party(a.source) if a.refresh_first_party else verify(a.source)))
    except (DistributionError,OSError,ValueError,KeyError,TypeError):print(json.dumps({'status':'FAIL','error':'first-party license or original notice provenance/bytes differ'}));raise SystemExit(2)
