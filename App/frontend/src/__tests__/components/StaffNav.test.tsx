/**
 * The operations console sidebar.
 *
 * Three things here are easy to break silently and expensive to notice: where
 * the search field sits (it was in the footer, below eight destinations, which
 * is the one place nobody looks for it), what the account row says (the letters
 * come from the ROLE — a token carries no person's name, so initials of one
 * would be invented), and whether the menu behind that row still offers the
 * four things the flat footer used to offer outright.
 */
import React from "react";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { StaffNav, type StaffIdentity } from "../../components/StaffGuard";
import { getSidebarMode } from "../../lib/sidebarMode";

const ADMIN: StaffIdentity = {
  authenticated: true,
  role: "ura_admin",
  email: "officer.admin@ura.go.ug",
  external_id: "auth0|6a7d7af3",
  tenant_id: "default",
};

function renderNav(who: StaffIdentity = ADMIN) {
  const onOpenPalette = vi.fn();
  const onOpenSettings = vi.fn();
  const view = render(
    <StaffNav
      who={who}
      current="/admin"
      connected
      onOpenPalette={onOpenPalette}
      onOpenSettings={onOpenSettings}
    />,
  );
  return { ...view, onOpenPalette, onOpenSettings };
}

/* The row names itself with what it shows — "Officer · Admin" — with the dot
   and the avatar hidden from the a11y tree, so the accessible name is the two
   words. The address is the row's title and the menu's first line. */
function accountRow() {
  return screen.getByRole("button", { name: "Officer · Admin" });
}

/** The menu is only reachable through the row that opens it. */
function openAccountMenu() {
  fireEvent.click(accountRow());
  return screen.getByRole("menu", { name: "Account" });
}

beforeEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
});

describe("StaffNav", () => {
  it("puts search at the top, above every destination", () => {
    renderNav();
    const search = screen.getByRole("button", { name: "Search" });
    const overview = screen.getByRole("link", { name: "Overview" });
    // Node.compareDocumentPosition: 4 = "search precedes overview".
    expect(search.compareDocumentPosition(overview) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("offers the search field as a field, not a keyboard chord", () => {
    renderNav();
    const search = screen.getByRole("button", { name: "Search" });
    expect(within(search).getByText("Search")).toBeInTheDocument();
    // The ⌘K chip was Mac notation on a console most officers drive with a
    // mouse — and wrong for the Windows machines it actually runs on.
    expect(search.textContent).not.toMatch(/⌘|K$/);
  });

  it("opens the palette from the search field", () => {
    const { onOpenPalette } = renderNav();
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    expect(onOpenPalette).toHaveBeenCalled();
  });

  it("pins and unpins the rail from the head control", () => {
    renderNav();
    const toggle = screen.getByRole("button", { name: /Keep the sidebar open/ });
    expect(toggle).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(toggle);
    expect(screen.getByRole("button", { name: /Collapse the sidebar/ })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(getSidebarMode()).toBe("always-open");
  });

  it("names the account by its type, not by a name it does not have", () => {
    renderNav();
    const row = accountRow();
    expect(row).toHaveAttribute("title", "officer.admin@ura.go.ug");
    expect(within(row).getByText("AD")).toBeInTheDocument();
    expect(row.textContent).toContain("Officer");
    expect(row.textContent).toContain("Admin");
  });

  it("falls back to the type alone when the token carries no email", () => {
    renderNav({ authenticated: true, role: "ura_staff", external_id: "auth0|6a7d7af3" });
    const row = screen.getByRole("button", { name: "Staff" });
    expect(within(row).getByText("ST")).toBeInTheDocument();
    expect(within(row).getByText("Staff")).toBeInTheDocument();
  });

  it("keeps settings, theme, language, help and sign-out behind the account row", () => {
    renderNav();
    const menu = openAccountMenu();
    for (const label of ["Settings", "Theme", "Language", "Get help", "Sign out"]) {
      expect(within(menu).getByRole("menuitem", { name: label })).toBeInTheDocument();
    }
  });

  /* The theme control was a lone icon button in the footer toolbar that cycled
     three states behind one glyph. The toolbar is gone; both of its occupants
     moved onto/behind the account row. */
  it("moves the theme control off the rail and into the menu", () => {
    const { container } = renderNav();
    expect(container.querySelector(".staff-rail-tools")).toBeNull();
    expect(screen.queryByTitle(/^Theme:/)).toBeNull();

    const menu = openAccountMenu();
    fireEvent.click(within(menu).getByRole("menuitem", { name: "Theme" }));
    expect(within(menu).getByRole("menuitemradio", { name: /Auto/ })).toHaveAttribute(
      "aria-checked",
      "true",
    );

    fireEvent.click(within(menu).getByRole("menuitemradio", { name: "Dark" }));
    fireEvent.click(within(menu).getByRole("menuitem", { name: "Theme" }));
    expect(within(menu).getByRole("menuitemradio", { name: "Dark" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
  });

  it("shows only one expanding section at a time", () => {
    renderNav();
    const menu = openAccountMenu();
    fireEvent.click(within(menu).getByRole("menuitem", { name: "Theme" }));
    fireEvent.click(within(menu).getByRole("menuitem", { name: "Language" }));
    expect(within(menu).queryByRole("menuitemradio", { name: /Auto/ })).not.toBeInTheDocument();
    expect(within(menu).getByRole("menuitemradio", { name: /English/ })).toBeInTheDocument();
  });

  it("carries the live-escalation dot on the account row", () => {
    const { container, rerender } = renderNav();
    const row = accountRow();
    const dot = within(row).getByTitle("Live escalations connected");
    expect(dot).toHaveClass("staff-live-dot", "is-on");

    rerender(
      <StaffNav who={ADMIN} current="/admin" connected={false} onOpenPalette={vi.fn()} onOpenSettings={vi.fn()} />,
    );
    const reconnecting = within(accountRow()).getByTitle(/Reconnecting/);
    expect(reconnecting).not.toHaveClass("is-on");
    // Nothing left loose in the footer for it to be misaligned against.
    expect(container.querySelector(".staff-rail-foot > *")).toHaveClass("staff-acct");
  });

  it("opens settings from the menu", () => {
    const { onOpenSettings } = renderNav();
    const menu = openAccountMenu();
    fireEvent.click(within(menu).getByRole("menuitem", { name: "Settings" }));
    expect(onOpenSettings).toHaveBeenCalled();
    expect(screen.queryByRole("menu", { name: "Account" })).not.toBeInTheDocument();
  });

  it("expands the language options in place and marks the current one", () => {
    renderNav();
    const menu = openAccountMenu();
    fireEvent.click(within(menu).getByRole("menuitem", { name: "Language" }));
    const english = within(menu).getByRole("menuitemradio", { name: /English/ });
    expect(english).toHaveAttribute("aria-checked", "true");

    fireEvent.click(within(menu).getByRole("menuitemradio", { name: /Luganda/ }));
    fireEvent.click(within(menu).getByRole("menuitem", { name: "Language" }));
    expect(within(menu).getByRole("menuitemradio", { name: /Luganda/ })).toHaveAttribute(
      "aria-checked",
      "true",
    );
  });
});
