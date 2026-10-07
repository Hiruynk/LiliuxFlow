"use client";
import {cloneElement,createElement,type ReactElement} from "react";
import {useTranslation} from "react-i18next";
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

type MessageDescriptor=string|{byValue:Record<string,string>;sourceChoiceProvenance?:"ui-producer"|"data"}|{sourceUiTemplates:readonly {key:string;pattern:string;parameter:string}[];sourceChoiceProvenance?:"ui-producer"|"data"}|{key:string;values?:Record<string,unknown>;sourceValuePolicy?:SourceValuePolicy};
type Descriptor=Record<string,MessageDescriptor>;
// content/info descriptors are emitted only for the source-bound tooltip helper
// props. The source compiler never treats generic payload content/info as copy.
const displayProps=new Set(["title","alt","label","placeholder","aria-label","aria-description","ariaLabel","emptyMessage","emptyText","loadingText","children","description","caption","tooltip","tooltipContent","content","info","hint","subtitle","searchPlaceholder","loadingMessage","noDataMessage","applyLabel","resetLabel","aria-valuetext"]);
/** Subscribes only the display element. The containing form, callbacks, values,
 * query keys and stream effects retain their existing references on locale changes. */
function LocalizedProps({__liliuxflowElement:element,__liliuxflowDisplayKeys:keys,...forwarded}:Record<string,unknown>&{__liliuxflowElement:ReactElement<Record<string,unknown>>;__liliuxflowDisplayKeys:Descriptor}){
 const {t,i18n}=useTranslation();
 const translated=Object.fromEntries(Object.entries(keys).flatMap(([prop,message])=>{
  if(!displayProps.has(prop))throw new Error("Non-display property in locale descriptor");
  const source=element.props[prop];
  if(typeof message!=="string"&&"sourceUiTemplates" in message){
   // Exact source-bound human captions retain their original optional prop and
   // signed numeric text. Only a matching display template replaces the prop.
   if(typeof source!=="string")return [];
   for(const template of message.sourceUiTemplates){const match=source.match(new RegExp(template.pattern));if(match)return [[prop,t(template.key,{[template.parameter]:match[1],interpolation:{escapeValue:false},returnObjects:false})]];}
   if(source&&message.sourceChoiceProvenance==="ui-producer")i18n.options.parseMissingKeyHandler?.("notice.unboundUiCopy");
   return [];
  }
  const key=typeof message==="string"?message:"byValue" in message?(typeof source==="string"?ownValue(message.byValue,source):undefined):message.key;
  // The finite branch map is emitted at this exact source display site. Unknown
  // runtime data is preserved, never searched in a general translation map.
  if(!key){if(typeof message!=="string"&&"byValue" in message&&message.sourceChoiceProvenance==="ui-producer"&&typeof source==="string"&&source)i18n.options.parseMissingKeyHandler?.("notice.unboundUiCopy");return [];}
  const values=typeof message!=="string"&&"values" in message?message.values:undefined;
  const policy=typeof message!=="string"&&"sourceValuePolicy" in message?message.sourceValuePolicy:undefined;
  return [[prop,t(key,{...sourceDisplayValues(t,values,policy,()=>{i18n.options.parseMissingKeyHandler?.("notice.unboundUiCopy");return t("error.generic");},i18n.language),interpolation:{escapeValue:false},returnObjects:false})]];
 }));
 // HTML derives option.value from text when no explicit value is present.
 // Preserve that original data value while translating its visible label.
 if(element.type==="option"&&element.props.value===undefined&&typeof element.props.children==="string")translated.value=element.props.children;
 // Base UI/asChild consumers compose handlers and refs on the wrapper. Forward
 // that already-composed result exactly once; never re-merge it with originals.
 return cloneElement(element,{...forwarded,...translated});
}
export function localizeProps(element:ReactElement<Record<string,unknown>>,keys:Descriptor){
 // Expose original props to Base UI's mergeProps and getReactElementRef, so its
 // normal handler precedence, preventBaseUIHandler and ref merging remain intact.
 const props:Record<string,unknown>&{__liliuxflowElement:ReactElement<Record<string,unknown>>;__liliuxflowDisplayKeys:Descriptor}={...element.props,__liliuxflowElement:element,__liliuxflowDisplayKeys:keys};
 // Passing key:null creates the literal React key "null". Omit absent keys so
 // adjacent original elements retain their normal positional reconciliation.
 if(element.key!==null)props.key=element.key;
 return createElement(LocalizedProps,props);
}
type Capture=(prop:string,key:string,segments:readonly string[],values:readonly string[],sourceValuePolicy?:SourceValuePolicy)=>string;
/** Source expressions are evaluated and coerced at their original prop position
 * once. The factory exposes their original prop text to Base UI while recording
 * named interpolation for the subscribing display wrapper. */
export function localizeGeneratedProps(factory:(capture:Capture)=>ReactElement<Record<string,unknown>>,keys:Descriptor){
 const captured:Descriptor={...keys};
 const capture:Capture=(prop,key,segments,values,sourceValuePolicy)=>{
  if(!displayProps.has(prop)||segments.length!==values.length+1)throw new Error("Invalid display template descriptor");
  captured[prop]={key,values:Object.fromEntries(values.map((value,i)=>['value'+i,value])),sourceValuePolicy};
  return segments.map((part,i)=>part+(values[i]??'')).join('');
 };
 const element=factory(capture);
 return localizeProps(element,captured);
}
