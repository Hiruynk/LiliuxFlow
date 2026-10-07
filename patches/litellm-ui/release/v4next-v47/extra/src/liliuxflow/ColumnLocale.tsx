"use client";
import {useTranslation}from 'react-i18next';
import {flexRender,type ColumnDef,type HeaderContext}from '@tanstack/react-table';
import {SourceChartLabel}from './SourceChartDisplay';
export function LocalizedColumnTitle({messageKey,fallback}:{messageKey?:string;fallback:React.ReactNode}){
 const{t}=useTranslation();return <>{messageKey?t(messageKey):fallback}</>;
}
export function LocalizedColumnHeader<TData,TValue>({column,context}:{column:ColumnDef<TData,TValue>;context:HeaderContext<TData,TValue>}){
 const{t}=useTranslation();const key=column.meta?.liliuxflowHeaderKey;
 return <SourceChartLabel value={key?t(key):flexRender(column.header,context)} sourceValue={key?null:context.column.id} channel="column"/>;
}
