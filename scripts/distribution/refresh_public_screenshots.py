# SPDX-License-Identifier: Apache-2.0
"""Curate new source-pack screenshots from reviewed, four-language README refs.

Historical images remain on disk and in Git history. This generator updates
only the current public screenshot inventory and source-package selection.
"""
import argparse,hashlib,json,re
from pathlib import Path
from common import DistributionError,no_symlinks

ROOT=Path(__file__).resolve().parents[2]
READMES=('README.md','README.zh-Hant.md','README.zh-Hans.md','README.ja.md')

def refresh(source=ROOT):
 source=no_symlinks(source);manifest_path=source/'manifests/distribution/public-screenshots.json';policy_path=source/'manifests/distribution/source-allowlist.json'
 manifest=json.loads(manifest_path.read_text());policy=json.loads(policy_path.read_text())
 references=[set(re.findall(r'assets/screenshots/[^)\s"<>]+',(source/name).read_text())) for name in READMES]
 if not references[0] or any(items!=references[0] for items in references):raise DistributionError('four README screenshot references differ')
 selected=references[0];by_path={row['path']:row for row in manifest['screenshots']}
 if len(by_path)!=len(manifest['screenshots']) or not selected.issubset(by_path):raise DistributionError('README screenshot lacks reviewed provenance')
 for name in sorted(selected):
  row=by_path[name];file=no_symlinks(source/name)
  if row.get('private_avatar_keys_hostname_included') is not False or not file.is_file() or file.stat().st_size!=row['bytes'] or hashlib.sha256(file.read_bytes()).hexdigest()!=row['sha256']:
   raise DistributionError('README screenshot privacy or original-byte provenance differs')
 obsolete=sorted(set(by_path)-selected)
 manifest['screenshots']=[by_path[name] for name in sorted(selected)]
 for key in ('exact_paths','required_paths'):
  policy[key]=[name for name in policy[key] if not name.startswith('assets/screenshots/') or name in selected]
  for name in sorted(selected):
   if name not in policy[key]:policy[key].append(name)
 policy['exclude_paths']=sorted(set(policy.get('exclude_paths',[]))|set(obsolete))
 manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n');policy_path.write_text(json.dumps(policy,ensure_ascii=False,indent=2)+'\n')
 return {'status':'REFRESHED_CURRENT_SCREENSHOT_SELECTION','included':sorted(selected),'excluded_historical':obsolete,'image_bytes_modified':False,'images_deleted':False,'Git_history_rewritten':False}

if __name__=='__main__':
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--source',type=Path,default=ROOT);args=parser.parse_args();print(json.dumps(refresh(args.source),indent=2))
