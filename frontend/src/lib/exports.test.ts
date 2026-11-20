import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { apiFetchFile } = vi.hoisted(() => ({ apiFetchFile: vi.fn() }));
vi.mock("@/lib/api", () => ({ apiFetchFile }));

import { downloadExport, EXPORT_LABELS } from "./exports";

describe("downloadExport", () => {
  const createObjectURL = vi.fn(() => "blob:saved-file");
  const revokeObjectURL = vi.fn();
  // The unit tests run without a browser, so the link the browser would click is a fake.
  const link = { href: "", download: "", click: vi.fn(), remove: vi.fn() };
  const appendChild = vi.fn();

  beforeEach(() => {
    apiFetchFile.mockReset();
    createObjectURL.mockClear();
    revokeObjectURL.mockClear();
    Object.assign(link, { href: "", download: "" });
    link.click.mockClear();
    link.remove.mockClear();
    appendChild.mockClear();
    vi.stubGlobal("URL", { createObjectURL, revokeObjectURL });
    vi.stubGlobal("document", { createElement: () => link, body: { appendChild } });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("asks for the file of this kind and saves it under the backend's name", async () => {
    const blob = new Blob(["a,b"]);
    apiFetchFile.mockResolvedValue({ blob, filename: "blue-bottle-reviews.csv" });

    await downloadExport("t1", "reviews");

    expect(apiFetchFile).toHaveBeenCalledWith("/api/v1/targets/t1/export/reviews");
    expect(createObjectURL).toHaveBeenCalledWith(blob);
    expect(link.href).toBe("blob:saved-file");
    expect(link.download).toBe("blue-bottle-reviews.csv");
    expect(link.click).toHaveBeenCalledOnce();
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:saved-file");
  });

  it("uses a plain name when the backend sends none", async () => {
    apiFetchFile.mockResolvedValue({ blob: new Blob(["# x"]), filename: null });

    await downloadExport("t1", "analysis");

    expect(apiFetchFile).toHaveBeenCalledWith("/api/v1/targets/t1/export/analysis");
    expect(link.download).toBe("analysis.md");
  });

  it("leaves no link behind in the page", async () => {
    apiFetchFile.mockResolvedValue({ blob: new Blob(["x"]), filename: "x.csv" });

    await downloadExport("t1", "reviews");

    expect(appendChild).toHaveBeenCalledWith(link);
    expect(link.remove).toHaveBeenCalledOnce();
  });

  it("lets a failed request through, so the page can say so", async () => {
    apiFetchFile.mockRejectedValue(new Error("down"));

    await expect(downloadExport("t1", "reviews")).rejects.toThrow("down");
    expect(createObjectURL).not.toHaveBeenCalled();
  });

  it("labels each kind", () => {
    expect(EXPORT_LABELS).toEqual({
      reviews: "Export reviews (CSV)",
      analysis: "Export analysis (Markdown)",
    });
  });
});
