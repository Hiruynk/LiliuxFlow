import {createElement}from 'react';
import {NoticeBody}from './NoticeBody';
/** Explicit descriptor emitted at the source UI producer, before Sonner render.
 * Values are original template-coerced data; no model/input rewriting. */
export function noticeMessage(id:string,values?:Record<string,string>){return createElement(NoticeBody,{id,values});}
