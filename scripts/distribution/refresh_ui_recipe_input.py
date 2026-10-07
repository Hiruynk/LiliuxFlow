# SPDX-License-Identifier: Apache-2.0
"""Refresh a required UI recipe input using an exact prior canonical source tree.

No runtime build or source-tree mutation is performed. The prior indexed tree
must match its existing recipe digest before one reviewed source input changes.
"""
import argparse,hashlib,json,os
from pathlib import Path
from common import DistributionError,no_symlinks
from ui_recipe import UI_ROOTS

EXCLUDED={'.git','node_modules','out','.next','__pycache__'}
def sha(data):return hashlib.sha256(data).hexdigest()
def canonical_index(tree,prefix):
 entries=[]
 for directory,dirs,files in os.walk(tree,followlinks=False):
  dirs[:]=[name for name in dirs if name not in EXCLUDED and not (Path(directory)/name).is_symlink()]
  for name in files:
   file=Path(directory)/name
   if file.is_symlink() or name=='tsconfig.tsbuildinfo':continue
   data=file.read_bytes()
   if name=='next-env.d.ts':data=b'/// <reference types="next" />\n/// <reference types="next/image-types/global" />\n'
   entries.append({'path':prefix+'/'+file.relative_to(tree).as_posix(),'sha256':sha(data)})
 return sorted(entries,key=lambda row:row['path'])
def refresh(source,manifest,app,source_input,prior_tree):
 source=no_symlinks(source);manifest=no_symlinks(manifest);prior_tree=no_symlinks(prior_tree)
 value=json.loads(manifest.read_text());record=value['apps'][app];entries=canonical_index(prior_tree,UI_ROOTS[app]);before=sha(json.dumps(entries,separators=(',',':')).encode())
 if before!=record['source_tree_sha256']:raise DistributionError('prior canonical UI tree differs from the existing recipe')
 matches=[row for row in record['extra_files'] if row['source']==source_input]
 if len(matches)!=1:raise DistributionError('UI input must be one finite extra-file source')
 item=matches[0];target=next((row for row in entries if row['path']==item['target']),None)
 if target is None or target['sha256']!=item['sha256']:raise DistributionError('prior source input differs from its recipe')
 replacement=no_symlinks(source/source_input).read_bytes();item['sha256']=sha(replacement);target['sha256']=item['sha256'];record['source_tree_sha256']=sha(json.dumps(entries,separators=(',',':')).encode())
 manifest.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
 return {'status':'REFRESHED_EXACT_REQUIRED_UI_INPUT','manifest':manifest.relative_to(source).as_posix(),'app':app,'input':source_input,'before_tree_sha256':before,'after_tree_sha256':record['source_tree_sha256'],'input_sha256':item['sha256'],'runtime_build':False}
if __name__=='__main__':
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--source',type=Path,required=True);parser.add_argument('--manifest',type=Path,required=True);parser.add_argument('--app',choices=tuple(UI_ROOTS),required=True);parser.add_argument('--input',required=True);parser.add_argument('--prior-tree',type=Path,required=True);args=parser.parse_args();print(json.dumps(refresh(args.source,args.manifest,args.app,args.input,args.prior_tree),indent=2))
