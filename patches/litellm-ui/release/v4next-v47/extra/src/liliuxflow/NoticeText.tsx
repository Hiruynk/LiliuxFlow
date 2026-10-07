"use client";
import {useTranslation,I18nContext}from 'react-i18next';
import {useContext,isValidElement,type ReactNode}from 'react';
import sourceKeys from './notice-source-map.json';
import {recordUnboundNotice}from './core.mjs';
const keys:Record<string,string>=sourceKeys;
export function NoticeText({message,kind='error',provenance='source-ui'}:{message:ReactNode;kind?:'success'|'info'|'warning'|'error';provenance?:'source-ui'|'backend-diagnostic'}){
 const{t}=useTranslation();const context=useContext(I18nContext);
 if(isValidElement(message))return <>{message}</>;
 if(typeof message==='string'){
  if(provenance==='backend-diagnostic')return <details><summary>{t('notice.technicalDetails')}</summary><pre className="max-h-40 overflow-auto whitespace-pre-wrap text-xs">{message}</pre></details>;
  const key=keys[message];if(key)return <>{t(key)}</>;
  if(!context?.i18n)throw new Error('Locale provider missing for notice accounting');
  recordUnboundNotice(context.i18n);
  return <>{t('notice.'+kind)}</>;
 }
 return <>{message}</>;
}
