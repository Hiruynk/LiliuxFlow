// SPDX-License-Identifier: Apache-2.0
"use client";
import {useEffect,useRef,useState} from 'react';
import {modelInfoCall} from '@/components/networking';
import {uiText,useLocaleRevision} from './runtime';
import {parseSharedSummary} from './sharedSummary.mjs';
export default function SharedContextSummary({accessToken}:{accessToken:string|null}) {
 const flight=useRef<symbol|null>(null);
 useLocaleRevision();const [snapshot,setSnapshot]=useState<any>(null);const [unavailable,setUnavailable]=useState(false);
 useEffect(()=>{
  let stopped=false;const owner=Symbol();let timer:ReturnType<typeof setTimeout>|undefined;
  const clear=()=>{if(timer!==undefined){clearTimeout(timer);timer=undefined;}};
  const schedule=()=>{
   clear();
   if(!stopped&&!document.hidden&&accessToken)timer=setTimeout(()=>{timer=undefined;void poll();},2000);
  };
  async function poll(){
   clear();
   if(stopped||document.hidden||!accessToken)return;
   if(flight.current!==null){if(flight.current!==owner)schedule();return;}
   flight.current=owner;let again=false;
   try {
    const response=await modelInfoCall(accessToken,'','',1,50);
    const hasShared=response?.data?.some((row:any)=>/^qwen3\.8-flash-next-lily-q4-mtp2-(64k|128k|262k)$/.test(row?.model_name));
    const value=hasShared?parseSharedSummary(response?.liliuxflow_shared):null;
    again=Boolean(hasShared);
    if(!stopped){setSnapshot(value);setUnavailable(Boolean(hasShared&&!value));}
   } catch {again=true;if(!stopped){setUnavailable(true);setSnapshot(null);}}
   finally {if(flight.current===owner)flight.current=null;if(again)schedule();}
  }
  const visible=()=>{clear();if(!document.hidden)void poll();};
  void poll();document.addEventListener('visibilitychange',visible);
  return()=>{stopped=true;clear();document.removeEventListener('visibilitychange',visible);};
 },[accessToken]);
 return <SharedContextView snapshot={snapshot} unavailable={unavailable}/>;
}
export function SharedContextView({snapshot,unavailable=false}:{snapshot:any;unavailable?:boolean}) {
 if(!snapshot&&!unavailable)return null;
 return <section className="shrink-0 border-b px-4 py-2 text-xs" data-shared-context-summary>
  <strong>{uiText('shared.context.title')}</strong>
  {!snapshot?<p>{uiText('shared.context.unavailable')}</p>:<>
   <p>{uiText('shared.context.oneModel')} {uiText('shared.context.memoryUnavailable')}</p>
   <p>{uiText('shared.context.resident',{count:snapshot.engine.resident_context_tokens.toLocaleString()})}</p>
   <p>{uiText('shared.context.counts',snapshot.engine)}</p>
   {snapshot.engine.busy&&<p>{uiText('shared.context.busy')}</p>}
   <div className="flex flex-wrap gap-2">{snapshot.profiles.map((row:any)=><span className="rounded border p-1" key={row.model}><code>{row.model}</code> · {row.context_tokens.toLocaleString()} {uiText('shared.context.tokens')} · {uiText('shared.context.counts',row)}</span>)}</div>
  </>}
 </section>;
}
