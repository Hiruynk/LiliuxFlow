"use client";
import {Languages} from "lucide-react";
import {useTranslation} from "react-i18next";
import {DropdownMenu,DropdownMenuTrigger,DropdownMenuContent,DropdownMenuRadioGroup,DropdownMenuRadioItem} from "@/components/ui/dropdown-menu";
import {Button} from "@/components/ui/button";
import {AUTONYMS,LOCALES,isLocale,cookieValue} from "./core.mjs";
import {runtime} from "./runtime";
export function LanguageSelector(){
 const {t,i18n}=useTranslation();
 const locale=isLocale(i18n.language)?i18n.language:"zh-Hant";
 return <DropdownMenu><DropdownMenuTrigger render={<Button variant="ghost" className="gap-2 whitespace-nowrap" aria-label={t("locale.label")} />}><Languages className="size-4"/><span>{AUTONYMS[locale as keyof typeof AUTONYMS]}</span></DropdownMenuTrigger><DropdownMenuContent align="end" className="min-w-[160px]"><DropdownMenuRadioGroup value={locale} onValueChange={value=>{if(isLocale(value)){document.cookie=cookieValue(value);void runtime.changeLanguage(value);}}}>{LOCALES.map(value=><DropdownMenuRadioItem key={value} value={value}>{AUTONYMS[value as keyof typeof AUTONYMS]}</DropdownMenuRadioItem>)}</DropdownMenuRadioGroup></DropdownMenuContent></DropdownMenu>;
}
