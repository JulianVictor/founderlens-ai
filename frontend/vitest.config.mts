import { defineConfig } from "vitest/config";
import { fileURLToPath } from "node:url";

export default defineConfig({
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  test: {
    // Only the pure modules are unit-tested. Components and server actions are
    // covered by lint, the type checker and the production build; testing them
    // properly needs a browser environment, which is not worth standing up for
    // the little logic they contain.
    include: ["src/**/*.test.ts"],
    environment: "node",
  },
});
