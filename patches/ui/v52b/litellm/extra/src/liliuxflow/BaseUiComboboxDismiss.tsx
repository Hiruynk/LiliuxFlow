"use client";
import * as React from "react";
import { useTranslation } from "react-i18next";
import { useMergedRefs } from "@base-ui/utils/useMergedRefs";
import { InputGroupInput } from "@/components/ui/input-group";

/** Base UI 1.6.0 has no label prop for its internal dismiss controls. Its
 * public render/ref API exposes the adjacent Input/Popup DOM nodes. Change
 * only the verified empty, visually hidden dismiss span beside those nodes;
 * preserve both controls, their handlers, focus, item values and message data.
 * No document scan, MutationObserver, innerHTML or text replacement is used. */
function useAdjacentDismissLabel<T extends HTMLElement>(node: React.RefObject<T | null>, side: "previous" | "next", open: boolean) {
  const { t, i18n } = useTranslation();
  React.useLayoutEffect(() => {
    const button = side === "previous" ? node.current?.previousElementSibling : node.current?.nextElementSibling;
    if (!(button instanceof HTMLElement) || button.tagName !== "SPAN" || button.getAttribute("role") !== "button" || button.textContent !== "" || button.style.position !== "absolute" || button.style.width !== "1px" || button.style.height !== "1px") return;
    if (button.getAttribute("aria-label") !== "Dismiss" && button.getAttribute("data-liliuxflow-dismiss-source") !== "base-ui-1.6.0") return;
    button.setAttribute("aria-label", t("ui.browser.combobox.dismissPopup"));
    button.setAttribute("data-liliuxflow-dismiss-source", "base-ui-1.6.0");
  }, [node, side, open, t, i18n.language]);
}

export const LocalizedComboboxInput = React.forwardRef<HTMLInputElement, React.ComponentProps<typeof InputGroupInput> & { __liliuxflowOpen: boolean }>(function LocalizedComboboxInput({ __liliuxflowOpen, ...props }, forwardedRef) {
  const ownRef = React.useRef<HTMLInputElement>(null);
  const mergedRef = useMergedRefs(ownRef, forwardedRef);
  useAdjacentDismissLabel(ownRef, "previous", __liliuxflowOpen);
  return <InputGroupInput {...props} ref={mergedRef}/>;
});

export const LocalizedComboboxPopup = React.forwardRef<HTMLDivElement, React.ComponentProps<"div"> & { __liliuxflowOpen: boolean }>(function LocalizedComboboxPopup({ __liliuxflowOpen, ...props }, forwardedRef) {
  const ownRef = React.useRef<HTMLDivElement>(null);
  const mergedRef = useMergedRefs(ownRef, forwardedRef);
  useAdjacentDismissLabel(ownRef, "next", __liliuxflowOpen);
  return <div {...props} ref={mergedRef}/>;
});

export const LocalizedComboboxChipsInput = React.forwardRef<HTMLInputElement, React.ComponentProps<"input"> & { __liliuxflowOpen: boolean }>(function LocalizedComboboxChipsInput({ __liliuxflowOpen, ...props }, forwardedRef) {
  const ownRef = React.useRef<HTMLInputElement>(null);
  const mergedRef = useMergedRefs(ownRef, forwardedRef);
  useAdjacentDismissLabel(ownRef, "previous", __liliuxflowOpen);
  return <input {...props} ref={mergedRef}/>;
});
