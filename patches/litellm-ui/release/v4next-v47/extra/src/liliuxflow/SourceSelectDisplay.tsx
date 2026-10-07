"use client";
import {cloneElement,createElement,useContext,type ReactElement,type ReactNode} from "react";
import {I18nContext} from "react-i18next";
import {recordUnboundNotice} from "./core.mjs";
import {LocalizedValue} from "./LocalizedText";
type Items=Record<string,ReactNode>|readonly {value:unknown;label:ReactNode}[];
/** Only source-bound finite UI option values are translated. The original items,
 * custom labels, values, callbacks and refs remain the caller's objects. */
type ElementProps=Record<string,unknown>&{placeholder?:ReactNode;children?:ReactNode|((value:unknown)=>ReactNode)};
function SourceSelectDisplay({__element:element,__items:items,__keys:keys,__owned:owned,...forwarded}:Record<string,unknown>&{
 __element:ReactElement<ElementProps>;__items:Items;__keys:Record<string,string>;__owned:readonly unknown[];
}){
 const context=useContext(I18nContext);
 const label=(value:unknown):ReactNode=>Array.isArray(items)?items.find(item=>Object.is(item.value,value))?.label:Object.hasOwn(items,String(value))?(items as Record<string,ReactNode>)[String(value)]:undefined;
 return cloneElement(element,{...forwarded,children:(value:unknown)=>{
  const child=element.props.children;
  const original=typeof child==='function'?child(value):child!=null?child:label(value)??element.props.placeholder??(typeof value==='string'?value:null);
  const isOwned=owned.some(candidate=>Object.is(candidate,value));
  const key=isOwned&&Object.hasOwn(keys,String(value))?keys[String(value)]:undefined;
  if(!key&&isOwned){if(!context?.i18n)throw new Error("Locale provider missing for source selection accounting");recordUnboundNotice(context.i18n);}
  return <LocalizedValue node={original} sourceDisplayChoices={key&&typeof original==='string'?{[original]:key}:undefined} sourceChoiceProvenance="data"/>;
 }});
}
export function sourceSelectDisplay(element:ReactElement<ElementProps>,items:Items,keys:Record<string,string>,owned:readonly unknown[]=Object.keys(keys)){
 const props:Record<string,unknown>={...element.props,__element:element,__items:items,__keys:keys,__owned:owned};if(element.key!==null)props.key=element.key;
 return createElement(SourceSelectDisplay,props as Parameters<typeof SourceSelectDisplay>[0]);
}
