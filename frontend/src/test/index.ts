// Everything an integration test needs, in one import:
//   import { render, screen, http, server, ingestionRun } from "@/test";
// Re-exporting Testing Library from one file is the "custom test utils" setup
// its docs recommend. Unit tests import the single file they need instead
// (e.g. "@/test/factories"), so they don't load React or MSW.
export * from "@testing-library/react";
export { http, HttpResponse } from "msw";
export * from "./factories";
export * from "./responses";
export * from "./server";
