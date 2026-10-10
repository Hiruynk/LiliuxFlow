// SPDX-License-Identifier: Apache-2.0
"use client";
import {useEffect,useRef,useState} from 'react';
import {uiText,useLocaleRevision} from './runtime';
const aliases=Object.freeze(['qwen3.8-flash-next-lily-q4-mtp2-64k','qwen3.8-flash-next-lily-q4-mtp2-128k','qwen3.8-flash-next-lily-q4-mtp2-262k']);
export const isMtp2Model=(model:unknown):boolean=>typeof model==='string'&&aliases.includes(model);
export function effectiveModelKey(model:unknown,memoryKey:string,original:string|null|undefined){return isMtp2Model(model)?memoryKey.trim():original;}
export function currentProxyBase(base:string,origin:string){
 const parsed=new URL(base||origin,origin);
 if(parsed.origin!==origin||!['http:','https:'].includes(parsed.protocol)||parsed.username||parsed.password)throw new Error('MTP2 proxy origin unavailable');
 return parsed.href.replace(/\/$/,'');
}
export function useSharedOwnKey<T extends {model_group:string}>(loadModels:(key:string)=>Promise<T[]>,getBase:()=>string){
 const [key,setValue]=useState('');const [models,setModels]=useState<T[]>([]);const [unavailable,setUnavailable]=useState(false);
 const current=useRef('');const generation=useRef(0);const accepted=useRef('');const alive=useRef(true);
 useEffect(()=>{alive.current=true;return()=>{alive.current=false;generation.current++;current.current='';accepted.current='';};},[]);
 const setKey=(value:string)=>{generation.current++;current.current=value;accepted.current='';setValue(value);setModels([]);setUnavailable(false);};
 const load=async()=>{
  const value=current.current.trim();if(accepted.current===value&&models.length)return;
  const request=++generation.current;
  if(!value){setModels([]);return;}
  if(value.length>256||![...value].every(x=>x.charCodeAt(0)>=0x21&&x.charCodeAt(0)<=0x7e)){setUnavailable(true);return;}
  try{
   currentProxyBase(getBase(),window.location.origin);
   const rows=await loadModels(value);
   if(!alive.current||generation.current!==request)return;
   const finite=rows.filter(row=>isMtp2Model(row.model_group));
   if(finite.length>3||new Set(finite.map(row=>row.model_group)).size!==finite.length)throw new Error('MTP2 catalog unavailable');
   accepted.current=value;setModels(finite);setUnavailable(finite.length===0);
  }catch{if(alive.current&&generation.current===request){setModels([]);setUnavailable(true);}}
 };
 const keyFor=(model:unknown,original:string|null|undefined)=>effectiveModelKey(model,
  accepted.current===current.current.trim()&&models.some(row=>row.model_group===model)?current.current:'',original);
 const proxyBase=()=>currentProxyBase(getBase(),window.location.origin);
 return {key,setKey,load,models,unavailable,keyFor,proxyBase};
}
export function SharedOwnKeyInput({state}:{state:{key:string;setKey:(value:string)=>void;load:()=>Promise<void>;unavailable:boolean}}){
 useLocaleRevision();
 return <div className="relative mt-2" data-mtp2-virtual-key>
  <label className="mb-2 block text-sm font-medium">{uiText('shared.context.title')} · {uiText('shared.context.virtualKey')}</label>
  <input className="h-8 w-full rounded-md border bg-background px-3 text-sm" type="password" autoComplete="off"
   aria-label={uiText('shared.context.virtualKey')} value={state.key} onChange={event=>state.setKey(event.target.value)} onBlur={()=>void state.load()}/>
  {state.unavailable&&<p className="text-xs">{uiText('shared.context.unavailable')}</p>}
 </div>;
}
