import { describe, expect, it, vi } from "vitest";
import { makeThumbnail, stripStepPrefix } from "../../lib/attachments";
import { connectorMonogram } from "../../lib/connectors";

describe("stripStepPrefix", () => {
  it.each([
    ["Step 1: Select your bank.", "Select your bank."],
    ["step 2. Click Generate PRN", "Click Generate PRN"],
    ["Step 10) Wait for the receipt", "Wait for the receipt"],
    ["  Step 3 - Download the slip", "Download the slip"],
    ["Step 4 – Sign out", "Sign out"],
  ])("removes the number the list already prints from %j", (input, expected) => {
    expect(stripStepPrefix(input)).toBe(expected);
  });

  it("leaves a sentence that merely mentions a step alone", () => {
    expect(stripStepPrefix("Check the Step 2 form first.")).toBe("Check the Step 2 form first.");
    expect(stripStepPrefix("Stepping through the menu")).toBe("Stepping through the menu");
  });

  it("leaves an empty step empty", () => {
    expect(stripStepPrefix("")).toBe("");
  });
});

describe("connectorMonogram", () => {
  it.each([
    ["EFRIS", "EF"],
    ["Digital Tax Stamps", "DT"],
    ["TIN services", "TS"],
    ["Payments", "PA"],
    ["URSB", "UR"],
    ["x", "X"],
  ])("gives %j the tile %j", (label, expected) => {
    expect(connectorMonogram(label)).toBe(expected);
  });
});

describe("makeThumbnail", () => {
  const picture = new File(["pixels"], "portal.png", { type: "image/png" });

  it("gives no thumbnail, and does not throw, where the browser cannot decode images", async () => {
    vi.stubGlobal("createImageBitmap", undefined);
    await expect(makeThumbnail(picture)).resolves.toBeUndefined();
    vi.unstubAllGlobals();
  });

  it("gives no thumbnail for a file that will not decode", async () => {
    vi.stubGlobal("createImageBitmap", () => Promise.reject(new Error("bad image")));
    await expect(makeThumbnail(picture)).resolves.toBeUndefined();
    vi.unstubAllGlobals();
  });

  it("crops to a square data: URL and releases the bitmap", async () => {
    const close = vi.fn();
    const drawImage = vi.fn();
    vi.stubGlobal("createImageBitmap", () => Promise.resolve({ width: 200, height: 100, close }));
    const canvas = {
      width: 0,
      height: 0,
      getContext: () => ({ drawImage }),
      toDataURL: () => "data:image/jpeg;base64,AAAA",
    };
    const create = vi.spyOn(document, "createElement").mockReturnValue(canvas as unknown as HTMLElement);

    await expect(makeThumbnail(picture, 40)).resolves.toBe("data:image/jpeg;base64,AAAA");

    expect([canvas.width, canvas.height]).toEqual([40, 40]);
    // 200x100 covers a 40 square at scale 0.4: 80x40, centred, so x is -20.
    expect(drawImage).toHaveBeenCalledWith(expect.anything(), -20, 0, 80, 40);
    expect(close).toHaveBeenCalledTimes(1);
    create.mockRestore();
    vi.unstubAllGlobals();
  });
});
