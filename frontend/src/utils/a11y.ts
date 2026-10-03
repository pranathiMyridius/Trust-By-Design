import type { KeyboardEvent } from "react";

/*
 * Stage 19 (accessibility): helpers for elements that must stay a
 * <div>/<tr> for layout reasons but are clickable. Spreading
 * `clickableProps(handler)` gives them a button role, puts them in the
 * tab order and activates them with Enter / Space like a real button.
 *
 * Key presses that originate on a nested interactive control (e.g. an
 * "Analyze" button inside a clickable row) are ignored so that control
 * keeps its own behaviour.
 */
export function activateOnKey(handler: () => void) {
  return (event: KeyboardEvent<HTMLElement>) => {
    if (event.target !== event.currentTarget) {
      return;
    }
    if (event.key === "Enter" || event.key === " " || event.key === "Spacebar") {
      event.preventDefault();
      handler();
    }
  };
}

export function clickableProps(
  handler: () => void,
  options: { role?: "button" | "link"; label?: string } = {}
) {
  return {
    role: options.role ?? "button",
    tabIndex: 0,
    "aria-label": options.label,
    onClick: handler,
    onKeyDown: activateOnKey(handler),
  } as const;
}

/*
 * Roving arrow-key navigation for role="tablist" containers: Left/Right
 * (and Up/Down) move focus between enabled tabs, Home/End jump to the
 * ends. Activation is left to Enter/Space/click (manual activation), so
 * attach this as the tablist's onKeyDown.
 */
export function handleTablistKeyDown(event: KeyboardEvent<HTMLElement>) {
  const keys = ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End"];
  if (!keys.includes(event.key)) {
    return;
  }

  const tabs = Array.from(
    event.currentTarget.querySelectorAll<HTMLElement>('[role="tab"]')
  ).filter((tab) => tab.getAttribute("aria-disabled") !== "true" && !tab.hasAttribute("disabled"));

  if (tabs.length === 0) {
    return;
  }

  const currentIndex = tabs.indexOf(document.activeElement as HTMLElement);
  let nextIndex: number;

  if (event.key === "Home") {
    nextIndex = 0;
  } else if (event.key === "End") {
    nextIndex = tabs.length - 1;
  } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
    nextIndex = currentIndex <= 0 ? tabs.length - 1 : currentIndex - 1;
  } else {
    nextIndex = currentIndex >= tabs.length - 1 ? 0 : currentIndex + 1;
  }

  event.preventDefault();
  tabs[nextIndex]?.focus();
}
