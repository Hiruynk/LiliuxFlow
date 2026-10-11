// SPDX-License-Identifier: Apache-2.0
// User-entered virtual key lives only in this page's module memory.
let key='';let revision=0;
/** @param {unknown} model */
export const isSharedAlias=model=>typeof model==='string'&&/^qwen3\.8-flash-next-lily-q4-mtp2-(64k|128k|262k)$/.test(model);
export const keyRevision=()=>revision;
/** @param {unknown} value */
export function setSharedKey(value){key=typeof value==='string'?value.trim():'';revision++;}
/** @returns {Record<string,string>} */
export const sharedKeyHeaders=()=>key?{Authorization:'Bearer '+key}:{};
/** @param {string} model @param {string} endpoint @returns {Record<string,string>} */
export function sharedInferenceHeaders(model,endpoint){
 if(!isSharedAlias(model))return {};
 if(endpoint!=='v1/chat/completions')throw new Error('SHARED_CHAT_ONLY');
 if(!key)throw new Error('SHARED_KEY_REQUIRED');
 return sharedKeyHeaders();
}
/** @param {any} response @returns {import("../lib/types").Model[]} */
export function sharedModelRecords(response){
 if(!response || !Array.isArray(response.data))throw new Error('Invalid model catalog');
 /** @type {Set<string>} */
 const seen=new Set();
 /** @type {Record<string,number>} */
 const contexts={'64k':65536,'128k':131072,'262k':262144};
 return response.data.filter((/** @type {any} */ row)=>typeof row?.id==='string'&&isSharedAlias(row.id)&&!seen.has(row.id)&&seen.add(row.id)).map((/** @type {any} */ row)=>({id:row.id,name:row.id,state:'unknown',playgroundType:'model',description:'',unlisted:false,peerID:'',context_length:contexts[row.id.split('-').at(-1)],modalities:{in:['text','image'],out:['text']}}));
}
