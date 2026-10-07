"use client";
import {useTranslation} from "react-i18next";
export function BrandAttribution(){
 const {t}=useTranslation();
 return <footer className="flex flex-wrap items-center justify-center gap-x-3 gap-y-1 border-t border-border bg-background px-4 py-2 text-xs text-muted-foreground">
  <img src="/ui/liliuxflow-mark.svg" alt="" className="size-4 dark:brightness-0 dark:invert"/>
  <span>LiliuxFlow by Diurnoctra</span><span>{t("brand.tagline")}</span>
  <span>{t("brand.admin")}</span><span>{t("brand.manager")}</span><span>{t("brand.engine")}</span>
 </footer>;
}
