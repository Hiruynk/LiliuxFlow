# SPDX-License-Identifier: Apache-2.0
"""CPU contract: authenticated shared records must reach Manager model choices.

No build, browser, model, DB or network. Node imports the shipped converter.
The consumer fixture is the selector filter and the no-matcher/no-profile path
of buildModelOptions at pinned llama-swap fcefa7b7bebc326790937bbebc32ae87a958747c,
verified against materialized product UI sources:
ModelSelector.svelte SHA: 7bad0dcf733a9957fe1f3d54754cc2fe2c10133172cf6398d8453fdebcb0a473
modelUtils.ts SHA: 5d406e15eab4987d754922b8cd9df57894518450909da1ce64e3fcde2960393d

The fixture preserves visibility/unlisted/peer/alias behavior for these model
records. It intentionally exercises the consumer rather than asserting that
one producer field equals a literal.
"""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
MODULE=ROOT/'patches/ui/mtp2-shared/llama-swap/extra/src/liliuxflow/sharedKey.mjs'

CONSUMER_SCRIPT=r"""
import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
const {sharedModelRecords}=await import(pathToFileURL(process.argv[1]).href);
const aliases=['64k','128k','262k'].map(size=>'qwen3.8-flash-next-lily-q4-mtp2-'+size);
const baseline={id:'qwen3.8-flash-next-lily-q4-64k',playgroundType:'model',name:'MTP0',unlisted:false,peerID:''};
// Actual ModelSelector consumer predicate.
const selectable=rows=>rows.filter((model)=>model.playgroundType==='model'||model.playgroundType==='peer');
// Actual buildModelOptions no-matcher/no-profile path: groupModels+optionsFor.
function buildChoices(rows){
 const models=selectable(rows).filter(m=>!m.unlisted);
 const local=models.filter(m=>!m.peerID),peers=models.filter(m=>m.peerID);
 function optionsFor(models,group,withAliases){
  const options=[];
  for(const model of models){
   options.push({value:model.id,group,name:model.name&&model.name!==model.id?model.name:undefined});
   if(withAliases)for(const alias of model.aliases??[])options.push({value:alias,group,aliasOf:model.id});
  }
  return options;
 }
 return [...optionsFor(local,'Local',true),...optionsFor(peers,'Peers',false)];
}
const records=sharedModelRecords({data:aliases.map(id=>({id}))});
assert.deepEqual(buildChoices([baseline,...records]).map(row=>row.value),[baseline.id,...aliases]);
assert.deepEqual(records.map(row=>row.context_length),[65536,131072,262144]);
// Negative control reproduces the real drop and proves the consumer gate matters.
const incomplete=records.map(({playgroundType,...rest})=>rest);
assert.deepEqual(buildChoices([baseline,...incomplete]).map(row=>row.value),[baseline.id]);
// Projection adds only explicitly returned finite granted aliases.
const subset=sharedModelRecords({data:[{id:aliases[1]},{id:aliases[1]},{id:'foreign-model'},{id:baseline.id}]});
assert.deepEqual(buildChoices([baseline,...subset]).map(row=>row.value),[baseline.id,aliases[1]]);
assert.deepEqual(buildChoices([baseline,...sharedModelRecords({data:[]})]).map(row=>row.value),[baseline.id]);
console.log(JSON.stringify({status:'PASS',granted_choices:3,subset_choices:1,missing_type_negative_control:true}));
"""

class SharedSelectorContract(unittest.TestCase):
 def test_authenticated_shared_records_survive_actual_model_selector_contract(self):
  node=shutil.which('node')
  self.assertIsNotNone(node,'Node is required for the shared UI consumer CPU contract; do not silently skip')
  result=subprocess.run([node,'--input-type=module','-e',CONSUMER_SCRIPT,str(MODULE)],capture_output=True,text=True,timeout=10)
  self.assertEqual(result.returncode,0,result.stderr)
  report=json.loads(result.stdout)
  self.assertEqual(report,{'status':'PASS','granted_choices':3,'subset_choices':1,'missing_type_negative_control':True})

if __name__=='__main__':unittest.main(verbosity=2)
