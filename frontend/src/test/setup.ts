// Vitest setup (Slice 4b-2): jest-dom matchers on vitest's expect + RTL cleanup between tests.
import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(() => cleanup());
