"use client";
import {useTranslation} from "react-i18next";
import displayMoment from "moment/min/moment-with-locales";
import {parseExpiresUtc} from "@/utils/keyExpiryUtils";
import type {ReactNode} from "react";
/** Display-only. Original formatter result remains a fallback; original parsing,
 * ISO form values, ranges, expiration decisions and callbacks are untouched. */
export function LocalizedDateValue({node,value,mode}:{node:ReactNode;value:Date|string|number|null|undefined|{from?:Date;to?:Date};mode:"date"|"date-time"|"expiry-date-time"|"date-range"}){
 const {t,i18n}=useTranslation();
 if(i18n.language==='en')return <>{node}</>;
 const locale=i18n.language==='ja'?'ja':i18n.language==='zh-Hans'?'zh-cn':'zh-tw';
 if(value==null||value==='')return <>{node}</>;
 if(mode==='date-range'){
  const range=value as {from?:Date;to?:Date};if(!range.from||!range.to)return <>{node}</>;
  return <>{displayMoment(range.from).locale(locale).format('LL')} – {displayMoment(range.to).locale(locale).format('LL')}</>;
 }
 const source=mode==='expiry-date-time'&&typeof value==='string'?parseExpiresUtc(value):value as Date|string|number;
 const date=displayMoment(source).locale(locale);
 if(!date.isValid())return <>{node==='Invalid Date'?t('ui.datePicker.invalidDate'):node}</>;
 return <>{date.format(mode==='date'?'LL':'LL HH:mm:ss')}</>;
}
