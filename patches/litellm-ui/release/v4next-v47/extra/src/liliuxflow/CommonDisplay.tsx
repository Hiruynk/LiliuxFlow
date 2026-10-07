"use client";
import {cloneElement,createElement,useContext,type ReactElement,type ReactNode} from "react";
import {useTranslation,I18nContext} from "react-i18next";
import {recordUnboundNotice} from "./core.mjs";
import maps from "./final-common-display-map.json";
const choices:Record<string,Record<string,string>>=maps.choices;
type Provenance="source-ui"|"raw-data";
function useCommonDisplay(){
 const{t}=useTranslation();const context=useContext(I18nContext);
 return {t,resolve:(scope:keyof typeof maps.choices,node:ReactNode,provenance:Provenance="source-ui"):ReactNode=>{
  if(provenance==="raw-data"||typeof node!=="string"||!node)return node;
  // This one punctuation marker is the exact DateCell default, not copy.
  if(scope==="dateFallbacks"&&node==="-")return node;
  const key=choices[scope][node];if(key)return t(key,{interpolation:{escapeValue:false},returnObjects:false});
  if(!context?.i18n)throw new Error("Locale provider missing for common display accounting");
  recordUnboundNotice(context.i18n);return t("error.generic");
 }};
}
export function CommonChoice({scope,node,provenance="source-ui"}:{scope:keyof typeof maps.choices;node:ReactNode;provenance?:Provenance}){
 const{resolve}=useCommonDisplay();return <>{resolve(scope,node,provenance)}</>;
}
export function SortDirectionText({label,descending}:{label:string;descending:boolean}){
 const{t,resolve}=useCommonDisplay();const display=resolve("sortLabels",label);
 return <>{t(descending?maps.sortDesc:maps.sortAsc,{value0:display,interpolation:{escapeValue:false},returnObjects:false})}</>;
}
function DisplayTrigger({__element:element,__fields:fields,...forwarded}:Record<string,unknown>&{__element:ReactElement<Record<string,unknown>>;__fields:readonly string[]}){
 const{t,resolve}=useCommonDisplay();
 const display=fields.map(field=>resolve("sortLabels",field)).join(t(maps.fieldOr));
 return cloneElement(element,{...forwarded,"aria-label":t(maps.sortOptions,{value0:display,interpolation:{escapeValue:false},returnObjects:false})});
}
export function localizeSortTrigger(factory:(capture:(fields:readonly string[])=>string)=>ReactElement<Record<string,unknown>>){
 let fields:readonly string[]|undefined;const element=factory(values=>{fields=values;return `Sort options for ${values.join(" or ")}`;});
 if(!fields)throw new Error("Sort display capture missing");
 const props:Record<string,unknown>={...element.props,__element:element,__fields:fields};if(element.key!==null)props.key=element.key;
 return createElement(DisplayTrigger,props as Parameters<typeof DisplayTrigger>[0]);
}
/** Original Date object/parse and local timezone stay unchanged; only visible
 * month/day/time formatting follows UI locale. Exported legacy formatters remain intact. */
export function CommonCellDate({date,precision="datetime",full=false}:{date:Date;precision?:"date"|"datetime";full?:boolean}){
 const{i18n}=useTranslation();const locale=i18n.language==="en"?"en-US":i18n.language==="ja"?"ja-JP":i18n.language==="zh-Hans"?"zh-CN":"zh-TW";
 const options:Intl.DateTimeFormatOptions={month:"short",day:"numeric",...(precision==="date"||full?{year:"numeric"}:{}),...(precision==="datetime"||full?{hour:"2-digit",minute:"2-digit",second:"2-digit",hourCycle:"h23"}:{})};
 const display=new Intl.DateTimeFormat(locale,options).format(date);
 return <>{display}{full?` (${Intl.DateTimeFormat().resolvedOptions().timeZone})`:""}</>;
}
export function BudgetLimitText({budget,original}:{budget:number|null;original:string}){
 const{t}=useTranslation();if(budget===null)return <>{t(maps.budgetUnlimited)}</>;
 // The original formatter already produced the exact amount after 'of $'.
 return <>{t(maps.budgetOf,{value0:original.slice(4),interpolation:{escapeValue:false},returnObjects:false})}</>;
}
function BudgetMeterDisplay({__element:element,__values:values,...forwarded}:Record<string,unknown>&{__element:ReactElement<Record<string,unknown>>;__values:{spend:string;budget:string}}){
 const{t}=useTranslation();return cloneElement(element,{...forwarded,"aria-valuetext":t(maps.budgetFraction,{value0:values.spend,value1:values.budget,interpolation:{escapeValue:false},returnObjects:false})});
}
export function localizeBudgetMeter(factory:(capture:(spend:string,budget:string)=>string)=>ReactElement<Record<string,unknown>>){
 let values:{spend:string;budget:string}|undefined;const element=factory((spend,budget)=>{values={spend,budget};return `${spend} of $${budget}`;});if(!values)throw new Error("Budget display capture missing");
 const props:Record<string,unknown>={...element.props,__element:element,__values:values};if(element.key!==null)props.key=element.key;return createElement(BudgetMeterDisplay,props as Parameters<typeof BudgetMeterDisplay>[0]);
}
export function FilterLabel({original,uiOwned}:{original:string;uiOwned:boolean}){
 return uiOwned?<CommonChoice scope="filterLabels" node={original}/>:<>{original}</>;
}
function FilterRemoveDisplay({__element:element,__label:label,__uiOwned:uiOwned,...forwarded}:Record<string,unknown>&{__element:ReactElement<Record<string,unknown>>;__label:string;__uiOwned:boolean}){
 const{t,resolve}=useCommonDisplay();
 return cloneElement(element,{...forwarded,"aria-label":t(maps.removeFilter,{value0:resolve("filterLabels",label,uiOwned?"source-ui":"raw-data"),interpolation:{escapeValue:false},returnObjects:false})});
}
export function localizeFilterRemove(factory:(capture:(label:string,uiOwned:boolean)=>string)=>ReactElement<Record<string,unknown>>){
 let captured:{label:string;uiOwned:boolean}|undefined;const element=factory((label,uiOwned)=>{captured={label,uiOwned};return `Remove ${label} filter`;});if(!captured)throw new Error("Filter display capture missing");
 const props:Record<string,unknown>={...element.props,__element:element,__label:captured.label,__uiOwned:captured.uiOwned};if(element.key!==null)props.key=element.key;return createElement(FilterRemoveDisplay,props as Parameters<typeof FilterRemoveDisplay>[0]);
}
