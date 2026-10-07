import {createInstance} from 'i18next';
/** @typedef {'en'|'zh-Hant'|'zh-Hans'|'ja'} Locale */
/** @typedef {Record<Locale, Record<string,string>>} Catalogs */
/** @type {readonly Locale[]} */
export const LOCALES = Object.freeze(['en', 'zh-Hant', 'zh-Hans', 'ja']);
export const AUTONYMS = Object.freeze({en:'English','zh-Hant':'繁體中文','zh-Hans':'简体中文',ja:'日本語'});
export const COOKIE = 'liliuxflow_locale';
/** @type {WeakMap<import('i18next').i18n,{knownMissing:Map<string,number>,undeclared:number}>} */
const diagnostics=new WeakMap();
/** Key identifiers and counts only; never interpolation values or unknown input.
 * @param {import('i18next').i18n} runtime */
export function getLocaleDiagnostics(runtime) {
  const state=diagnostics.get(runtime);
  return {knownMissingKeys:state?[...state.knownMissing].map(([key,count])=>({key,count})):[],undeclaredKeyCount:state?.undeclared??0,unresolvedCount:state?[...state.knownMissing.values()].reduce((a,b)=>a+b,0)+state.undeclared:0};
}
/** @param {unknown} value @returns {value is Locale} */
export function isLocale(value) { return typeof value==='string' && LOCALES.some(locale=>locale===value); }
/** @param {unknown} value @returns {Locale|null} */
export function mapBrowserLocale(value) {
  const tag = String(value ?? '').toLowerCase();
  if (tag === 'en' || tag.startsWith('en-')) return 'en';
  if (tag === 'ja' || tag.startsWith('ja-')) return 'ja';
  if (/^zh-(hans|cn|sg)(-|$)/.test(tag)) return 'zh-Hans';
  if (tag === 'zh' || /^zh-(hant|tw|hk|mo)(-|$)/.test(tag)) return 'zh-Hant';
  return null;
}
/** @param {unknown} cookie @returns {Locale|null} */
export function readPreference(cookie) {
  const matches = String(cookie ?? '').split(';').map(p=>p.trim()).filter(p=>p.startsWith(COOKIE+'='));
  if (matches.length !== 1) return null;
  const value=matches[0].slice(COOKIE.length+1);
  return isLocale(value) ? value : null;
}
/** @param {string} cookie @param {readonly string[]} languages @returns {Locale} */
export function initialLocale(cookie, languages=[]) {
  const saved=readPreference(cookie);
  if(saved) return saved;
  for(const language of languages){const locale=mapBrowserLocale(language);if(locale)return locale;}
  return 'zh-Hant';
}
/** @param {string} locale */
export function cookieValue(locale) {
  if (!isLocale(locale)) throw new TypeError('Unsupported locale');
  return `${COOKIE}=${locale}; Path=/; Max-Age=31536000; SameSite=Lax`;
}
/** @param {Catalogs} catalogs */
export function validateCatalogs(catalogs) {
  const keys=Object.keys(catalogs['zh-Hant'] ?? {}).sort();
  if(!keys.length) throw new Error('Empty catalog');
  /** @type {(s:string)=>string} */
  const params=s=>[...s.matchAll(/{{\s*([^}]+?)\s*}}/g)].map(m=>m[1]).sort().join('|');
  for(const locale of LOCALES) {
    const catalog=catalogs[locale];
    if(!catalog || JSON.stringify(Object.keys(catalog).sort())!==JSON.stringify(keys)) throw new Error(`Catalog key mismatch: ${locale}`);
    for(const key of keys) if(typeof catalog[key]!=='string' || !catalog[key].trim() || params(catalog[key])!==params(catalogs['zh-Hant'][key])) throw new Error(`Catalog value mismatch: ${locale}:${key}`);
  }
  return keys;
}
/** React Trans supports prop overrides; prohibit them in product catalogs.
 * @param {Catalogs} catalogs @param {Record<string,readonly string[]>} componentManifest */
export function validateRichCatalogs(catalogs,componentManifest) {
  for(const [key,names] of Object.entries(componentManifest)) {
    for(const locale of LOCALES) {
      const text=catalogs[locale][key];
      if(typeof text!=='string'||!text.trim())throw new Error('Missing rich translation: '+locale+':'+key);
      if(/<!|<\?/.test(text))throw new Error('Invalid rich markup directive: '+locale+':'+key);
      const seen=[];
      // A source English comparison may contain a literal '<' before a slot.
      // Inspect actual alphabetic/closing tag shapes; literal math stays text.
      for(const match of text.matchAll(/<\s*[A-Za-z/][^>]*>/g)) {
        const tag=match[0].match(/^<([A-Za-z][A-Za-z0-9]*)\/>$/);
        if(!tag||!names.includes(tag[1]))throw new Error('Invalid rich component marker: '+locale+':'+key);
        seen.push(tag[1]);
      }
      if(JSON.stringify(seen.sort())!==JSON.stringify([...names].sort()))throw new Error('Rich component mismatch: '+locale+':'+key);
    }
  }
}
/** Count unbound UI producer copy without retaining the string or parameters.
 * @param {import('i18next').i18n} instance */
export function recordUnboundNotice(instance){
 const state=diagnostics.get(instance);
 if(!state)throw new Error('Unregistered locale runtime');
 const key='notice.unboundUiCopy';state.knownMissing.set(key,(state.knownMissing.get(key)??0)+1);
}
/** @param {Catalogs} catalogs @param {string} locale @param {{declaredKeys?:readonly string[]}} options */
export function createLocaleRuntime(catalogs,locale='zh-Hant',options={}) {
  const catalogKeys=validateCatalogs(catalogs);
  if(!isLocale(locale)) throw new TypeError('Unsupported locale');
  const instance=createInstance();
  const declared=new Set([...catalogKeys,...(options.declaredKeys??[])]);
  const state={knownMissing:new Map(),undeclared:0};diagnostics.set(instance,state);
  instance.init({resources:Object.fromEntries(LOCALES.map(l=>[l,{translation:catalogs[l]}])),lng:locale,supportedLngs:[...LOCALES],load:'currentOnly',fallbackLng:false,parseMissingKeyHandler:key=>{
    if(declared.has(key))state.knownMissing.set(key,(state.knownMissing.get(key)??0)+1);else state.undeclared++;
    const lang=isLocale(instance.language)?instance.language:'zh-Hant';return {en:'Unable to display this message.','zh-Hant':'無法顯示此訊息。','zh-Hans':'无法显示此消息。',ja:'このメッセージを表示できません。'}[lang];
  },initAsync:false,keySeparator:false,nsSeparator:false,interpolation:{escapeValue:true},returnNull:false});
  return instance;
}
/** Only reads/writes the enum preference; owns no session, credential or API client.
 * @param {import('i18next').i18n} runtime @param {Window} win */
export function connectBrowser(runtime,win=window) {
  const doc=win.document;
  // Read-only browser evidence: numbers only, never source keys or values.
  const exposeCounts=()=>{const report=getLocaleDiagnostics(runtime);doc.documentElement.dataset.liliuxflowUnresolvedCount=String(report.unresolvedCount);doc.documentElement.dataset.liliuxflowUndeclaredCount=String(report.undeclaredKeyCount);};
  /** @type {(locale:string)=>void} */
  const apply=locale=>{if(isLocale(locale)&&locale!==runtime.language)runtime.changeLanguage(locale);doc.documentElement.lang=runtime.language;exposeCounts();};
  apply(initialLocale(doc.cookie,win.navigator.languages));
  const sync=()=>apply(readPreference(doc.cookie)??runtime.language);
  const visibleSync=()=>{if(doc.visibilityState==='visible')sync();};
  const change=()=>{doc.documentElement.lang=runtime.language;exposeCounts();};
  win.addEventListener('focus',sync);doc.addEventListener('visibilitychange',visibleSync);
  runtime.on('languageChanged',change);
  const timer=win.setInterval(visibleSync,1000);
  return {
    /** @param {string} locale */
    select(locale) { if(!isLocale(locale))throw new TypeError('Unsupported locale');doc.cookie=cookieValue(locale);apply(locale); },
    dispose() {win.clearInterval(timer);win.removeEventListener('focus',sync);doc.removeEventListener('visibilitychange',visibleSync);runtime.off('languageChanged',change);},
  };
}
