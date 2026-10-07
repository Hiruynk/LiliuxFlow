"use client";
import {createContext,useContext,type ReactNode} from "react";
import {I18nContext,useTranslation} from "react-i18next";
import {recordUnboundNotice} from "./core.mjs";

type Labels={category?:Readonly<Record<string,string>>;index?:Readonly<Record<string,string>>;suggestion?:Readonly<Record<string,string>>;column?:Readonly<Record<string,string>>;caption?:Readonly<Record<string,string>>};
const SourceChartContext=createContext<Labels|undefined>(undefined);
/** Exact source-owned UI series opt in at their caller. The provider carries
 * only message keys; chart config, category keys, payloads and callbacks stay raw.
 * Locale subscriptions live in the receiving display leaves, so this provider
 * never keys, remounts or reruns the containing data/query/stream component. */
export function SourceChartDisplay({labels,children}:{labels:Labels;children:ReactNode}){
 return <SourceChartContext.Provider value={labels}>{children}</SourceChartContext.Provider>;
}
/** No source scope means raw custom model/tool labels. An unknown label inside
 * a finite UI scope remains visible and is counted as missing display coverage. */
export function SourceChartLabel({value,sourceValue=value,channel="category"}:{value:ReactNode;sourceValue?:ReactNode;channel?:keyof Labels}){
 const labels=useContext(SourceChartContext)?.[channel];
 const {t}=useTranslation();const locale=useContext(I18nContext);
 if(!labels||typeof sourceValue!=="string"||!sourceValue||typeof value!=="string")return <>{value}</>;
 // The raw category remains the chart/accessor key. Formatting may change its
 // displayed case; resolve the source key before using the original fallback.
 const key=Object.hasOwn(labels,sourceValue)?labels[sourceValue]:undefined;if(key)return <>{t(key,{interpolation:{escapeValue:false},returnObjects:false})}</>;
 if(!locale?.i18n)throw new Error("Locale provider missing for source chart accounting");
 recordUnboundNotice(locale.i18n);return <>{value}</>;
}
