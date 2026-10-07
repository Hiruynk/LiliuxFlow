"use client";
import {cloneElement, createElement, isValidElement, useContext, type ReactElement, type ReactNode} from "react";
import {I18nContext, useTranslation} from "react-i18next";
import {recordUnboundNotice} from "./core.mjs";
import {NoticeText} from "./NoticeText";
import bindings from "./source-display-field-map.json";

type Scope = keyof typeof bindings.scopes;
const scopes:Record<string,Record<string,string>> = bindings.scopes;
const retained:Record<string,readonly string[]> = bindings.retained;
function useDisplayResolver(){
  const {t}=useTranslation();
  // Accounting uses the provider's original instance, not useTranslation's wrapper.
  const context=useContext(I18nContext);
  return (scope:Scope,value:unknown):ReactNode=>{
    if(typeof value!=="string" || !value)return value as ReactNode;
    if(retained[scope]?.includes(value))return value;
    const key=scopes[scope]?.[value];
    if(key)return t(key,{interpolation:{escapeValue:false},returnObjects:false});
    if(!context?.i18n)throw new Error("Locale provider missing for source display accounting");
    recordUnboundNotice(context.i18n);
    return t("error.generic");
  };
}
/** Only exact, reviewed UI configuration/producer values may enter this boundary.
 * Never pass model output, user text, schema keys, option values or API diagnostics. */
export function SourceDisplayText({scope,value}:{scope:Scope;value:ReactNode}){
  const display=useDisplayResolver();
  return <>{isValidElement(value)?value:display(scope,value)}</>;
}
const displayProperties=new Set(["placeholder","title","aria-label","alt"]);
function SourceDisplayProps({__sourceElement:element,__sourceScope:scope,__sourceFields:fields,...forwarded}:Record<string,unknown>&{
  __sourceElement:ReactElement<Record<string,unknown>>;__sourceScope:Scope;__sourceFields:readonly string[];
}){
  const display=useDisplayResolver();
  const translated=Object.fromEntries(fields.map(prop=>{
    if(!displayProperties.has(prop))throw new Error("Non-display property at source display boundary");
    return [prop,display(scope,element.props[prop])];
  }));
  // Base UI may compose refs and handlers onto this wrapper. Forward that result
  // once, exactly as LocalizedProps does; original non-display props stay intact.
  return cloneElement(element,{...forwarded,...translated});
}
export function sourceDisplayProps(element:ReactElement<Record<string,unknown>>,scope:Scope,fields:readonly string[]){
  const props:Record<string,unknown>={...element.props,__sourceElement:element,__sourceScope:scope,__sourceFields:fields};
  if(element.key!==null)props.key=element.key;
  return createElement(SourceDisplayProps,props as Parameters<typeof SourceDisplayProps>[0]);
}
/** RHF error objects/messages stay original. Required errors are proven local
 * requiredRule/native-rule results; custom validator errors need producer proof. */
export function SourceValidationText({error,provenance="source-validation",validationScope="required"}:{
  error?:{type?:string;message?:string};validationScope?:"required"|"login"|"memory"|"team-create";provenance?:"source-validation"|"backend-diagnostic";
}){
  const {t}=useTranslation();const context=useContext(I18nContext);
  if(!error?.message)return null;
  if(provenance==="backend-diagnostic")return <NoticeText message={error.message} provenance="backend-diagnostic"/>;
  const login:Record<string,string>={"Please enter your username":"ui.login.validation.username","Please enter your password":"ui.login.validation.password"};
  const memory:Record<string,string>={"Key is required": "ui.nested.source.bd07d1371045", "Value is required": "ui.nested.source.1fbf278b5399"};
  const key=validationScope==="team-create"&&error.type==="too_small"&&error.message==="Please input a team name"?"ui.teamCreate.validation.name":validationScope==="memory"&&error.type==="too_small"?memory[error.message]:validationScope==="login"&&error.type==="too_small"?login[error.message]:error.type==="required"?scopes.validation[error.message]:undefined;
  if(key)return <>{t(key)}</>;
  if(!context?.i18n)throw new Error("Locale provider missing for validation accounting");
  recordUnboundNotice(context.i18n);
  return <>{t("error.generic")}</>;
}
