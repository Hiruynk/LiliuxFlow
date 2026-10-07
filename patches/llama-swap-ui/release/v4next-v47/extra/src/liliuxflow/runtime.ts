import {createLocaleRuntime,initialLocale,cookieValue,isLocale} from "./core.mjs";
import {localeStore,translationStore} from "./svelte.mjs";
import en from "./catalogs/en.json";
import hant from "./catalogs/zh-Hant.json";
import hans from "./catalogs/zh-Hans.json";
import ja from "./catalogs/ja.json";
import declaredKeys from "./source-message-keys.json";
export const runtime=createLocaleRuntime({en,"zh-Hant":hant,"zh-Hans":hans,ja},initialLocale(document.cookie,navigator.languages),{declaredKeys});
// Apply the saved/browser preference synchronously before Svelte mounts.
document.documentElement.lang=runtime.language;
export const locale=localeStore(runtime);
export const uiText=translationStore(runtime);
export function chooseLocale(value:string){if(isLocale(value)){document.cookie=cookieValue(value);void runtime.changeLanguage(value);}}
