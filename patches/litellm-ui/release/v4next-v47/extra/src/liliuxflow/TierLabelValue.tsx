"use client";
import {useTranslation} from "react-i18next";
import type {ReactNode} from "react";
import choices from "./tier-label-keys.json";
/** Display only. A custom label/name is data even when it equals a default word. */
export function TierLabelValue({node,tier,customLabels,row}:{node:ReactNode;tier?:string;customLabels?:Partial<Record<string,string>>;row?:{id:string;name:string}}){
 const {t,i18n}=useTranslation();const id=tier??row?.id;
 if(row&&row.name.trim()&&row.name.trim()!==row.id)return <>{node}</>;
 if(id&&customLabels?.[id]?.trim())return <>{node}</>;
 if(row&&row.name.trim()&&!Object.hasOwn(choices.tiers,row.id))return <>{node}</>;
 const key=typeof node==='string'?(choices.labels as Record<string,string>)[node]:undefined;
 if(key)return <>{t(key)}</>;
 if(typeof node!=='string'||!node)return <>{node}</>;
 i18n.options.parseMissingKeyHandler?.('notice.unboundUiCopy');return <>{t('error.generic')}</>;
}
