import { defineConfig } from "@trigger.dev/sdk";
import { aptGet } from "@trigger.dev/build/extensions/core";

export default defineConfig({
  // Fill in from your Trigger.dev dashboard: Project settings -> Project ref.
  project: "proj_rkttmpuzxlbytvohzydp",
  dirs: ["./src"],
  maxDuration: 60,
  build: {
    // Trigger.dev's deployed runners only officially support Node - Python isn't there
    // by default. This installs python3 plus the scraper's two dependencies as Debian
    // packages directly (avoids needing a separate pip install step in the build).
    // Confirmed against https://trigger.dev/docs/config/extensions/aptGet on 2026-08-10.
    extensions: [aptGet({ packages: ["python3", "python3-requests", "python3-bs4"] })],
  },
});
