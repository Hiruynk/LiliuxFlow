"use client";
import {cloneElement,createElement,useContext,type ReactElement} from "react";
import {useTranslation,I18nContext} from "react-i18next";
import {recordUnboundNotice} from "./core.mjs";
import keys from "./final-nav-display-map.json";

type Captured={used:string;total:string};
function MeterDisplay({__element:element,__values:values,...forwarded}:Record<string,unknown>&{__element:ReactElement<Record<string,unknown>>;__values:Captured}){
 const{t}=useTranslation();
 return cloneElement(element,{...forwarded,"aria-valuetext":t(keys.meterFraction,{value0:values.used,value1:values.total,interpolation:{escapeValue:false},returnObjects:false})});
}
/** Restricted to the exact local UsageMeter source. Capture original formatted
 * numbers at their existing prop position; original value/max/handlers stay data. */
export function localizeUsageMeter(factory:(capture:(used:string,total:string)=>string)=>ReactElement<Record<string,unknown>>){
 let values:Captured|undefined;
 const element=factory((used,total)=>{values={used,total};return `${used} of ${total}`;});
 if(!values)throw new Error("UsageMeter display capture missing");
 const props:Record<string,unknown>={...element.props,__element:element,__values:values};
 if(element.key!==null)props.key=element.key;
 return createElement(MeterDisplay,props as Parameters<typeof MeterDisplay>[0]);
}
/** Inputs are only formatExpirationStatus output and its original license date.
 * Do not use this renderer for arbitrary API/model/user text. */
export function UsagePlanStatus({sourceStatus,expirationDate}:{sourceStatus:string;expirationDate?:string|null}){
 const{t,i18n}=useTranslation();const context=useContext(I18nContext);
 if(sourceStatus==="Active plan")return <>{t(keys.planActive)}</>;
 if(sourceStatus==="No expiration")return <>{t(keys.planNoExpiration)}</>;
 const status=sourceStatus.startsWith("Expired ")?"expired":sourceStatus.startsWith("Expires ")?"expires":null;
 if(status&&expirationDate){
  const date=new Date(`${expirationDate}T00:00:00Z`);
  if(!Number.isNaN(date.getTime())){
   // Original license formatter explicitly uses UTC; preserve that timezone.
   const locale=i18n.language==="en"?"en-US":i18n.language==="ja"?"ja-JP":i18n.language==="zh-Hans"?"zh-CN":"zh-TW";
   const display=new Intl.DateTimeFormat(locale,{year:"numeric",month:"short",day:"numeric",timeZone:"UTC"}).format(date);
   return <>{t(status==="expired"?keys.planExpired:keys.planExpires,{value0:display,interpolation:{escapeValue:false},returnObjects:false})}</>;
  }
 }
 if(!context?.i18n)throw new Error("Locale provider missing for plan display accounting");
 recordUnboundNotice(context.i18n);return <>{t("error.generic")}</>;
}
