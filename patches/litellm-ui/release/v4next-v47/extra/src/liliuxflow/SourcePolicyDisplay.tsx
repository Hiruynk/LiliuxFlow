"use client";
import {createContext,useContext,type ReactNode} from "react";
import {I18nContext,useTranslation} from "react-i18next";
import {recordUnboundNotice} from "./core.mjs";
import manifest from "./source-policy-rows.json";
import categoryKeys from "./source-policy-category-keys.json";

/** Compare complete source JSON objects without invoking getters/toJSON or
 * changing producer objects. Original array order and values are significant. */
export function canonicalPolicyRow(value:unknown):string|null{
 const seen=new Set<object>();
 function encode(node:unknown):string|null{
  if(node===null||typeof node==="string"||typeof node==="boolean")return JSON.stringify(node);
  if(typeof node==="number")return Number.isFinite(node)?JSON.stringify(node):null;
  if(typeof node!=="object"||node===null||seen.has(node))return null;
  if(Object.getOwnPropertySymbols(node).length)return null;
  const proto=Object.getPrototypeOf(node);
  if(!Array.isArray(node)&&proto!==Object.prototype&&proto!==null)return null;
  seen.add(node);
  const descriptors=Object.getOwnPropertyDescriptors(node);
  const keys=Array.isArray(node)?Array.from({length:node.length},(_,i)=>String(i)):Object.keys(node).sort();
  const names=Object.getOwnPropertyNames(node);
  if(Array.isArray(node)?names.some(name=>name!=="length"&&!keys.includes(name)):names.length!==keys.length){seen.delete(node);return null;}
  const parts:string[]=[];
  for(const key of keys){const descriptor=descriptors[key];if(!descriptor||!("value" in descriptor)){seen.delete(node);return null;}const encoded=encode(descriptor.value);if(encoded===null){seen.delete(node);return null;}parts.push(Array.isArray(node)?encoded:JSON.stringify(key)+":"+encoded);}
  seen.delete(node);
  return Array.isArray(node)?"["+parts.join(",")+"]":"{"+parts.join(",")+"}";
 }
 return encode(value);
}
const reviewedRows=new Map(manifest.rows.map(row=>[canonicalPolicyRow(row.source_row),row]));
function reviewedRow(template:unknown){const canonical=canonicalPolicyRow(template);return canonical===null?undefined:reviewedRows.get(canonical);}
const PolicyOwner=createContext<unknown>(undefined);
/** Receiving display scope; every original PolicyTemplateCard prop and callback
 * stays on the existing child. No ID/tag/title-only ownership inference. */
export function SourcePolicyTemplateDisplay({template,children}:{template:unknown;children:ReactNode}){
 return <PolicyOwner.Provider value={template}>{children}</PolicyOwner.Provider>;
}
export function SourcePolicyCopy({value,field,templates}:{value:ReactNode;field:"title"|"description"|"category";templates?:readonly unknown[]}){
 const {t}=useTranslation();const locale=useContext(I18nContext);const owner=useContext(PolicyOwner);
 if(typeof value!=="string")return <>{value}</>;
 let row=reviewedRow(owner);
 if(templates){
  // Shared raw category buckets remain raw if any contributing row is custom or
  // unreviewed. This leaves original counts, raw sort/filter keys and AND logic.
  const contributors=templates.filter(template=>{if(!template||typeof template!=="object")return false;const tags=Object.getOwnPropertyDescriptor(template,"tags");return tags&&"value" in tags&&Array.isArray(tags.value)&&tags.value.includes(value);});
  if(!contributors.length||contributors.some(template=>!reviewedRow(template)))return <>{value}</>;
  row=reviewedRow(contributors[0]);
 }
 if(!row)return <>{value}</>;
 if(field!=="category"&&row.source_row[field]!==value)return <>{value}</>;
 if(field==="category"&&!row.source_row.tags.includes(value))return <>{value}</>;
 const key=field==="title"?row.title_key:field==="description"?row.description_key:Object.hasOwn(categoryKeys,value)?(categoryKeys as Record<string,string>)[value]:undefined;
 if(!key){if(!locale?.i18n)throw new Error("Locale provider missing for source policy accounting");recordUnboundNotice(locale.i18n);}
 return <>{key?t(key,{interpolation:{escapeValue:false},returnObjects:false}):value}</>;
}
