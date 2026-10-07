"use client";
import {useEffect,useState} from "react";
import {I18nextProvider} from "react-i18next";
import {runtime} from "./runtime";
import {connectBrowser} from "./core.mjs";
import {BrandAttribution} from "./BrandAttribution";
export function LocaleProvider({children}:{children:React.ReactNode}) {
  const [ready,setReady]=useState(false);
  useEffect(()=>{const connection=connectBrowser(runtime);setReady(true);return ()=>connection.dispose();},[]);
  return <I18nextProvider i18n={runtime}>{ready?<>{children}<BrandAttribution/></>:<div role="status" aria-busy="true" aria-label="LiliuxFlow" className="flex min-h-screen items-center justify-center"><span aria-hidden="true" className="size-5 animate-spin rounded-full border-2 border-muted-foreground border-t-transparent" /></div>}</I18nextProvider>;
}
