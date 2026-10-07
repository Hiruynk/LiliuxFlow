"use client";
import {LocalizedText} from "./LocalizedText";
import {NoticeText} from "./NoticeText";
import type {ReactNode} from "react";
import policies from "./notice-display-policies.json";
type Policy={sourceValuePolicy?:{choices?:Record<string,Record<string,string>>;elideEnglishGrammar?:string[];unknownChoiceProvenance?:"ui-producer"|"data"};diagnostic?:{prefix:string;value:string;localCopies?:Record<string,string>}};
const bindings=policies as Record<string,Policy>;
export function NoticeBody({id,values}:{id:string;values?:Record<string,string>}){
 const policy=bindings[id];const diagnostic=policy?.diagnostic;
 if(diagnostic){const value=values?.[diagnostic.value]??"";const localKey=diagnostic.localCopies?.[value];
  return <><LocalizedText id={diagnostic.prefix}/>{localKey?<LocalizedText id={localKey}/>:<NoticeText message={value as ReactNode} provenance="backend-diagnostic"/>}</>;
 }
 return <LocalizedText id={id} values={values} sourceValuePolicy={policy?.sourceValuePolicy}/>;
}
