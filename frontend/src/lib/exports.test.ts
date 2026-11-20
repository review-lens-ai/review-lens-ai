import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { apiFetchFile } = vi.hoisted(() => ({ apiFetchFile: vi.fn() }));
vi.mock("@/lib/api", () => ({ apiFetchFile }));

import { cleanFilename, EXPORT_LABELS, fetchExport, saveFile } from "./exports";

describe("fetchExport", () => {
  beforeEach(() => {
    apiFetchFile.mockReset();
  });

  it("asks for the file of this kind and returns it with the backend's suggested name", async () => {
    const blob = new Blob(["a,b"]);
    apiFetchFile.mockResolvedValue({ blob, filename: "blue-bottle-reviews.csv" });

    const file = await fetchExport("t1", "reviews");

    expect(apiFetchFile).toHaveBeenCalledWith("/api/v1/targets/t1/export/reviews");
    expect(file).toEqual({ blob, filename: "blue-bottle-reviews.csv" });
  });

  it("suggests a plain name when the backend sends none", async () => {
    apiFetchFile.mockResolvedValue({ blob: new Blob(["# x"]), filename: null });

    const file = await fetchExport("t1", "analysis");

    expect(apiFetchFile).toHaveBeenCalledWith("/api/v1/targets/t1/export/analysis");
    expect(file.filename).toBe("analysis.md");
  });

  it("lets a failed request through, so the page can say so", async () => {
    apiFetchFile.mockRejectedValue(new Error("down"));

    await expect(fetchExport("t1", "reviews")).rejects.toThrow("down");
  });
});

describe("cleanFilename", () => {
  it("keeps a name that already ends in the right extension", () => {
    expect(cleanFilename("my-notes.csv", "reviews")).toBe("my-notes.csv");
  });

  it("adds the extension when the user left it off", () => {
    expect(cleanFilename("Smashburger notes", "reviews")).toBe("Smashburger notes.csv");
    expect(cleanFilename("summary", "analysis")).toBe("summary.md");
  });

  it("does not double the extension, whatever its case", () => {
    expect(cleanFilename("Notes.MD", "analysis")).toBe("Notes.md");
  });

  it("removes folder and reserved characters", () => {
    expect(cleanFilename("../my:file*?.csv", "reviews")).toBe("..-my-file--.csv");
  });

  it("falls back to a plain name when nothing is left", () => {
    expect(cleanFilename("   ", "reviews")).toBe("reviews.csv");
    expect(cleanFilename(".md", "analysis")).toBe("analysis.md");
  });
});

describe("saveFile", () => {
  const createObjectURL = vi.fn(() => "blob:saved-file");
  const revokeObjectURL = vi.fn();
  // The unit tests run without a browser, so the link the browser would click is a fake.
  const link = { href: "", download: "", click: vi.fn(), remove: vi.fn() };
  const appendChild = vi.fn();

  beforeEach(() => {
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

  it("saves the file under the given name", () => {
    const blob = new Blob(["a,b"]);

    saveFile(blob, "blue-bottle-reviews.csv");

    expect(createObjectURL).toHaveBeenCalledWith(blob);
    expect(link.href).toBe("blob:saved-file");
    expect(link.download).toBe("blue-bottle-reviews.csv");
    expect(link.click).toHaveBeenCalledOnce();
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:saved-file");
  });

  it("leaves no link behind in the page", () => {
    saveFile(new Blob(["x"]), "x.csv");

    expect(appendChild).toHaveBeenCalledWith(link);
    expect(link.remove).toHaveBeenCalledOnce();
  });
});

describe("EXPORT_LABELS", () => {
  it("labels each kind", () => {
    expect(EXPORT_LABELS).toEqual({
      reviews: "Reviews (CSV)",
      analysis: "Analysis (Markdown)",
    });
  });
});
