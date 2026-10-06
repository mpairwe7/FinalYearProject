/**
 * The response-language picker's Auto-detect option.
 *
 * Auto-detect is the default: the answer follows the language the taxpayer
 * types, and the code on the trigger is only the last language the assistant
 * answered in. Picking a language makes it the taxpayer's choice.
 */
import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import LanguageMenu from "../../components/LanguageMenu";
import { LOCALE_OPTIONS } from "../../lib/locales";

function renderMenu(props: Partial<React.ComponentProps<typeof LanguageMenu>> = {}) {
  const onLocaleChange = vi.fn();
  const onAutoDetect = vi.fn();
  render(
    <div className="chatv2">
      <LanguageMenu
        locale="lg"
        options={LOCALE_OPTIONS}
        onLocaleChange={onLocaleChange}
        autoDetect
        onAutoDetect={onAutoDetect}
        {...props}
      />
    </div>,
  );
  return { onLocaleChange, onAutoDetect };
}

describe("LanguageMenu auto-detect", () => {
  it("says on the trigger that the language is detected", () => {
    renderMenu();
    expect(
      screen.getByRole("button", { name: "Response language: Auto-detect (currently Luganda)" }),
    ).toBeInTheDocument();
    expect(screen.getByText("auto")).toBeInTheDocument();
  });

  it("checks Auto-detect rather than the detected language", () => {
    renderMenu();
    fireEvent.click(screen.getByRole("button", { name: /Response language/ }));
    expect(screen.getByRole("radio", { name: /Auto-detect/ })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("radio", { name: /Luganda/ })).toHaveAttribute("aria-checked", "false");
  });

  it("picking a language reports it; picking Auto-detect reports that", () => {
    const { onLocaleChange, onAutoDetect } = renderMenu({ autoDetect: false });
    fireEvent.click(screen.getByRole("button", { name: "Response language: Luganda" }));
    expect(screen.getByRole("radio", { name: /Luganda/ })).toHaveAttribute("aria-checked", "true");
    fireEvent.click(screen.getByRole("radio", { name: /Swahili/ }));
    expect(onLocaleChange).toHaveBeenCalledWith("sw");

    fireEvent.click(screen.getByRole("button", { name: "Response language: Luganda" }));
    fireEvent.click(screen.getByRole("radio", { name: /Auto-detect/ }));
    expect(onAutoDetect).toHaveBeenCalledTimes(1);
  });

  it("without onAutoDetect it is the plain three-language picker", () => {
    render(<LanguageMenu locale="en" options={LOCALE_OPTIONS} onLocaleChange={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Response language: English" }));
    expect(screen.getAllByRole("radio")).toHaveLength(LOCALE_OPTIONS.length);
    expect(screen.queryByRole("radio", { name: /Auto-detect/ })).not.toBeInTheDocument();
  });
});
