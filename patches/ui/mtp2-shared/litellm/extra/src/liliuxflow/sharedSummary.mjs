// SPDX-License-Identifier: Apache-2.0
const contexts=Object.freeze({'qwen3.8-flash-next-lily-q4-mtp2-64k':65536,'qwen3.8-flash-next-lily-q4-mtp2-128k':131072,'qwen3.8-flash-next-lily-q4-mtp2-262k':262144});
export function parseSharedSummary(value){
 if(!value)return null;
 const group=value.engine;const limits={active:2,pending:4,cleanup:6};
 if(!group||!['ready','loading','unloaded','unavailable'].includes(group.state)||group.resident_context_tokens!==262144||group.ram_bytes!==null||typeof group.busy!=='boolean'||!Array.isArray(value.profiles)||value.profiles.length<1||value.profiles.length>3)throw new Error('Invalid shared summary');
 const counts=row=>{const out={};for(const[key,max]of Object.entries(limits)){if(!Number.isInteger(row[key])||row[key]<0||row[key]>max)throw new Error('Invalid shared count');out[key]=row[key];}return out;};
 const seen=new Set();const profiles=value.profiles.map(row=>{if(!row||!Object.hasOwn(contexts,row.model)||seen.has(row.model)||row.context_tokens!==contexts[row.model])throw new Error('Invalid shared profile');seen.add(row.model);return Object.freeze({model:row.model,context_tokens:row.context_tokens,state:row.state,...counts(row)});});
 return Object.freeze({engine:Object.freeze({state:group.state,resident_context_tokens:262144,ram_bytes:null,busy:group.busy,...counts(group)}),profiles:Object.freeze(profiles)});
}
