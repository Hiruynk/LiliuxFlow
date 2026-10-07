"use client";
import {useTranslation} from "react-i18next";
import {NoticeText} from "./NoticeText";
const knownEnvCredentialFailures=new Set(["Invalid credentials used to access UI.\nCheck 'UI_USERNAME', 'UI_PASSWORD' in .env file", "Invalid credentials used to access UI. Check 'UI_USERNAME', 'UI_PASSWORD' in .env file", "Invalid credentials used to access UI. Check UI_USERNAME, UI_PASSWORD in .env file"]);
/** Display-only classification of the pinned login401 instruction. The original
 * error state and technical diagnostic remain unchanged and accessible. */
export function LoginFailureText({message}:{message:string}){
 const {t}=useTranslation();
 return <><span>{t("ui.browserContract.5a96e8452f1e")}</span>{knownEnvCredentialFailures.has(message)&&<span className="mt-1 block text-sm font-normal">{t("ui.browserContract.b1fcb17fc1c5")}</span>}<NoticeText message={message} provenance="backend-diagnostic"/></>;
}
