"use client";
import {useTranslation} from "react-i18next";
import {HTTPAccessErrorFallback} from "next/dist/client/components/http-access-fallback/error-fallback";
/** Pinned native Next 404 renderer; locale affects message/title only. */
export default function NotFound(){const {t}=useTranslation();return <HTTPAccessErrorFallback status={404} message={t("ui.browserContract.4197828a9971")}/>;}
