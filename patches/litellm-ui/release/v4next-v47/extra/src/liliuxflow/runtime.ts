import {useSyncExternalStore} from "react";
import {createLocaleRuntime,initialLocale} from "./core.mjs";
import en from "./catalogs/en.json";
import hant from "./catalogs/zh-Hant.json";
import hans from "./catalogs/zh-Hans.json";
import ja from "./catalogs/ja.json";
import declaredKeys from "./source-message-keys.json";
export const runtime=createLocaleRuntime({en,"zh-Hant":hant,"zh-Hans":hans,ja},typeof document==="undefined"?"zh-Hant":initialLocale(document.cookie,navigator.languages),{declaredKeys});
// Only insert the result as React text/attributes; React performs HTML escaping.
export const uiText=(key:string,params?:Record<string,unknown>)=>runtime.t(key,{...params,interpolation:{escapeValue:false}});
const subscribe=(notify:()=>void)=>{runtime.on("languageChanged",notify);return ()=>runtime.off("languageChanged",notify);};
export function useLocaleRevision(){return useSyncExternalStore(subscribe,()=>runtime.language,()=>"zh-Hant");}
