import { defineConfig } from "@trigger.dev/sdk";
import { aptGet, additionalFiles } from "@trigger.dev/build/extensions/core";

export default defineConfig({
  runtime: "node-22",
  project: "proj_rkttmpuzxlbytvohzydp",
  dirs: ["./src"],
  maxDuration: 60,
  build: {
    extensions: [
      aptGet({ packages: ["python3", "python3-requests", "python3-bs4"] }),
      additionalFiles({ files: ["./scraper/**"] }),
    ],
  },
});