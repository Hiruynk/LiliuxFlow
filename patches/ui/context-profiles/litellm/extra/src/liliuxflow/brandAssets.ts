/** Product defaults; custom nonempty branding remains supported. */
export const brandAssets = {
  light: "/ui/liliuxflow-logo-light.svg",
  dark: "/ui/liliuxflow-logo-dark.svg",
  favicon: "/ui/liliuxflow-favicon.svg",
  mark: "/ui/liliuxflow-mark.svg",
} as const;
export const brandAssetOrDefault = (url: string | null, fallback: string): string =>
  url?.trim() ? url : fallback;
