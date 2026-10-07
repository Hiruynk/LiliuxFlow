import {readable} from 'svelte/store';
/** @param {import('i18next').i18n} runtime */
export function localeStore(runtime) {
  return readable(runtime.language,set=>{
    /** @param {string} locale */
    const change=locale=>set(locale);runtime.on('languageChanged',change);
    return ()=>runtime.off('languageChanged',change);
  });
}
/** @param {import('i18next').i18n} runtime */
export function translationStore(runtime) {
  /** @param {string} locale */
  const forText=locale=>{
    const fixed=runtime.getFixedT(locale);
    /** @param {string} key @param {Record<string,unknown>} [options] */
    return (key,options)=>fixed(key,{...options,interpolation:{escapeValue:false}});
  };
  // These values are inserted through Svelte text expressions, which escape HTML.
  // Never pass this adapter's output to @html.
  return readable(forText(runtime.language),set=>{
    /** @param {string} locale */
    const change=locale=>set(forText(locale));runtime.on('languageChanged',change);
    return ()=>runtime.off('languageChanged',change);
  });
}
