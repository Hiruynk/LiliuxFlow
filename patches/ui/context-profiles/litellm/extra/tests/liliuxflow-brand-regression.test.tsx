import React from "react";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createInstance } from "i18next";
import { initReactI18next, I18nextProvider } from "react-i18next";
import { ThemeProvider, useTheme } from "@/contexts/ThemeContext";
import UIThemeSettings from "@/app/(dashboard)/ui-theme/UIThemeSettings";
import SidebarAccountMenu from "@/components/SidebarAccountMenu/SidebarAccountMenu";
import { BrandAttribution } from "@/liliuxflow/BrandAttribution";
import { brandAssets } from "@/liliuxflow/brandAssets";
import en from "@/liliuxflow/catalogs/en.json";
import hant from "@/liliuxflow/catalogs/zh-Hant.json";
import hans from "@/liliuxflow/catalogs/zh-Hans.json";
import ja from "@/liliuxflow/catalogs/ja.json";
vi.mock("@/components/networking", () => ({getProxyBaseUrl: () => "", getGlobalLitellmHeaderName: () => "Authorization"}));
vi.mock("@/app/(dashboard)/hooks/useAuthorized", () => ({default: () => ({userId: "synthetic-id", userEmail: "synthetic@example.invalid", userRoleLabel: "Admin", premiumUser: false})}));
vi.mock("@/app/(dashboard)/hooks/useDisableBlogPosts", () => ({useDisableBlogPosts: () => false}));
vi.mock("@/app/(dashboard)/hooks/useDisableShowPrompts", () => ({useDisableShowPrompts: () => false}));
vi.mock("@/app/(dashboard)/hooks/useDisableBouncingIcon", () => ({useDisableBouncingIcon: () => false}));
vi.mock("@/app/(dashboard)/hooks/useDisableShowNewBadge", () => ({useDisableShowNewBadge: () => false}));
const resources = {en: {translation: en}, "zh-Hant": {translation: hant}, "zh-Hans": {translation: hans}, ja: {translation: ja}};
async function wrapper(locale="en") {
  const i18n=createInstance();await i18n.use(initReactI18next).init({lng:locale,fallbackLng:"en",resources,interpolation:{escapeValue:false}});
  return ({children}: React.PropsWithChildren) => <I18nextProvider i18n={i18n}><ThemeProvider>{children}</ThemeProvider></I18nextProvider>;
}
function Probe() {
 const theme=useTheme();
 return <><img data-testid="light-logo" src={theme.logoUrl ?? ""} alt=""/><img data-testid="dark-logo" src={theme.logoUrlDark ?? ""} alt=""/><output data-testid="favicon">{theme.faviconUrl}</output><button onClick={() => {theme.setLogoUrl(null);theme.setLogoUrlDark("");theme.setFaviconUrl("  ");}}>Clear preview</button></>;
}
function defaults() {
 expect(screen.getByTestId("light-logo")).toHaveAttribute("src",brandAssets.light);
 expect(screen.getByTestId("dark-logo")).toHaveAttribute("src",brandAssets.dark);
 expect(screen.getByTestId("favicon")).toHaveTextContent(brandAssets.favicon);
}
beforeEach(() => {
 vi.stubGlobal("fetch",vi.fn().mockResolvedValue({ok:true,json:async()=>({values:{logo_url:null,logo_url_dark:null,favicon_url:null}})}));
 document.documentElement.classList.remove("dark");
});
describe("LiliuxFlow branding regression", () => {
 it.each(["en","zh-Hant","zh-Hans","ja"])("keeps defaults after navigating to empty theme settings and appearance changes in %s", async locale => {
  render(<><Probe/><UIThemeSettings userID="synthetic-id" userRole="Admin" accessToken="synthetic-token"/></>,{wrapper:await wrapper(locale)});
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
  await waitFor(defaults);
  for(const dark of [true,false,true]) {document.documentElement.classList.toggle("dark",dark);fireEvent.click(screen.getByText("Clear preview"));defaults();}
  expect(vi.mocked(fetch).mock.calls.every(([,options])=>options?.method==="GET")).toBe(true);
 });
 it("preserves configured URLs and resets local previews to product defaults", async () => {
  vi.stubGlobal("fetch",vi.fn().mockResolvedValue({ok:true,json:async()=>({values:{logo_url:"https://example.invalid/custom.svg",logo_url_dark:"https://example.invalid/dark.svg",favicon_url:"https://example.invalid/icon.svg"}})}));
  render(<Probe/>,{wrapper:await wrapper()});
  await waitFor(() => expect(screen.getByTestId("light-logo")).toHaveAttribute("src","https://example.invalid/custom.svg"));
  expect(screen.getByTestId("dark-logo")).toHaveAttribute("src","https://example.invalid/dark.svg");
  fireEvent.click(screen.getByText("Clear preview"));defaults();
 });
 it.each(["en","zh-Hant","zh-Hans","ja"])("uses product account header while preserving identity and upstream footer in %s",async locale=>{
  const onLogout=vi.fn();const user=userEvent.setup();
  render(<><SidebarAccountMenu onLogout={onLogout}/><BrandAttribution/></>,{wrapper:await wrapper(locale)});
  await user.click(screen.getByRole("button",{name:/synthetic@example.invalid/}));
  const panel=await screen.findByTestId("sidebar-account-menu-panel");
  expect(panel).toHaveTextContent("LiliuxFlow");expect(panel).not.toHaveTextContent("LiteLLM");expect(panel).not.toHaveTextContent("🌴");
  expect(panel.querySelector('a[href*="release_notes"]')).toBeNull();
  expect(panel).toHaveTextContent("synthetic@example.invalid");expect(panel).toHaveTextContent("synthetic-id");
  expect(panel.querySelector('img')).toHaveAttribute("src",brandAssets.mark);
  expect(screen.getByText(resources[locale as keyof typeof resources].translation["brand.admin"])).toBeInTheDocument();
  expect(onLogout).not.toHaveBeenCalled();
 });
});
