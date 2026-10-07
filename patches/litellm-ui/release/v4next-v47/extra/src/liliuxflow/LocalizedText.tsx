"use client";
import {useTranslation,TransWithoutContext} from "react-i18next";
import {cloneElement,isValidElement} from "react";
import type {TOptions} from "i18next";
/** Only the source compiler's exact reviewed UI producer sites use this policy.
 * Original expression values are captured once; custom names and API data stay raw. */
type SourceValuePolicy={choices?:Record<string,Record<string,string>>;elideEnglishGrammar?:readonly string[];unknownChoiceProvenance?:"ui-producer"|"data"};
const ownValue=<T,>(map:Record<string,T>|undefined,key:string):T|undefined=>map&&Object.hasOwn(map,key)?map[key]:undefined;
function sourceDisplayValues(t:(key:string)=>unknown,values:Record<string,unknown>|undefined,policy:SourceValuePolicy|undefined,onUnboundUiCopy:()=>unknown,locale:string){
 if(!policy)return values;
 return Object.fromEntries(Object.entries(values??{}).map(([name,value])=>{
  if(locale!=="en"&&policy.elideEnglishGrammar?.includes(name))return[name,""];
  const key=typeof value==='string'?ownValue(ownValue(policy.choices,name),value):undefined;
  if(!key&&typeof value==='string'&&value&&policy.choices?.[name]&&policy.unknownChoiceProvenance!=='data'){onUnboundUiCopy();return[name,value];}
  return[name,key?t(key):value];
 }));
}

export function LocalizedText({id,values,sourceValuePolicy}:{id:string;values?:TOptions;sourceValuePolicy?:SourceValuePolicy}){
 const {t,i18n}=useTranslation();
 return <>{t(id,{...sourceDisplayValues(t,values,sourceValuePolicy,()=>{i18n.options.parseMissingKeyHandler?.("notice.unboundUiCopy");return t("error.generic");},i18n.language),interpolation:{escapeValue:false},returnObjects:false})}</>;
}
export function LocalizedValue({node,elideEnglishPluralSuffix,sourceDisplayChoices,sourceChoiceProvenance="ui-producer",sourceRetainedChoices,sourceDisplayPrefixes,sourceUiTemplates,sourceChoiceEnabled=true}:{node:React.ReactNode;elideEnglishPluralSuffix?:true;sourceDisplayChoices?:Record<string,string>;sourceChoiceProvenance?:"ui-producer"|"data";sourceRetainedChoices?:readonly string[];sourceDisplayPrefixes?:Record<string,string>;sourceUiTemplates?:readonly {key:string;pattern:string;parameter:string}[];sourceChoiceEnabled?:boolean}){
 const {t,i18n}=useTranslation();
 // Only exact source-bound English plural suffix sites receive this flag. The
 // complete translated count sentence supplies the grammar in all three locales.
 // Original expressions still evaluate once before this display-only boundary.
 if(elideEnglishPluralSuffix)return i18n.language==="en"?<>{node}</>:null;
 // Only exact source-bound UI label producers receive a finite choice map.
 // Raw model/input/data nodes do not receive this map and remain unchanged.
 if(!sourceChoiceEnabled)return <>{node}</>;
 if(typeof node==='string'&&sourceRetainedChoices?.includes(node))return <>{node}</>;
 if(typeof node==='string'&&sourceDisplayPrefixes){
  const prefix=Object.keys(sourceDisplayPrefixes).find(p=>node.startsWith(p));
  if(prefix)return <>{t(sourceDisplayPrefixes[prefix])}{node.slice(prefix.length)}</>;
 }
 if(typeof node==='string'&&sourceUiTemplates){
  for(const template of sourceUiTemplates){const match=node.match(new RegExp(template.pattern));if(match)return <>{t(template.key,{[template.parameter]:match[1],interpolation:{escapeValue:false},returnObjects:false})}</>;}
 }
 const key=typeof node==='string'?ownValue(sourceDisplayChoices,node):undefined;
 if(!key&&typeof node==='string'&&node&&(sourceDisplayChoices||sourceDisplayPrefixes||sourceUiTemplates)&&sourceChoiceProvenance==='ui-producer'){i18n.options.parseMissingKeyHandler?.("notice.unboundUiCopy");return <>{node}</>;}
 return <>{key?t(key,{interpolation:{escapeValue:false},returnObjects:false}):node}</>;
}
function NamedSlot({node}:{node:React.ReactNode;slot:string}){return <>{node}</>;}
export function LocalizedRichText({id,values,children,components}:{id:string;values?:Record<string,unknown>;children?:React.ReactNode;components?:Record<string,React.ReactElement>}){
 const {t,i18n}=useTranslation();
 if(!i18n.exists(id))return <>{t(id,{interpolation:{escapeValue:false},returnObjects:false})}</>;
 // The upstream renderer assigns positional keys. Re-key its top-level named
 // slots so moving a data/control slot in another language preserves its node.
 const slots=Object.fromEntries(Object.entries(components??{}).map(([slot,node])=>[slot,<NamedSlot key={slot} slot={slot} node={node}/>]));
 const rendered=TransWithoutContext({t,i18n,i18nKey:id,values,components:slots,children,shouldUnescape:true});
 const content=Array.isArray(rendered)?rendered:[rendered];
 return <>{content.map(node=>isValidElement<{slot?:string}>(node)&&node.props.slot?cloneElement(node,{key:node.props.slot}):node)}</>;
}
