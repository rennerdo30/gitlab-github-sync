import { defineConfig } from "astro/config";
import starlight from "@astrojs/starlight";
import starlightThemeGalaxy from "starlight-theme-galaxy";

export default defineConfig({
  site: "https://rennerdo30.github.io/gitlab-github-sync",
  base: "/gitlab-github-sync",
  integrations: [
    starlight({
      title: "gitlab-github-sync",
      description:
        "Bidirectional mirroring of repositories, issues and merge requests between GitLab and GitHub, driven by the gh and glab CLIs.",
      plugins: [starlightThemeGalaxy()],
      customCss: ["./src/styles/custom.css"],
      social: [
        { icon: "github", label: "GitHub", href: "https://github.com/rennerdo30/gitlab-github-sync" },
      ],
      sidebar: [
        {
          label: "Getting Started",
          items: [
            { label: "Overview", slug: "index" },
            { label: "Installation", slug: "getting-started/installation" },
            { label: "Configuration", slug: "getting-started/configuration" },
          ],
        },
        {
          label: "Guides",
          items: [
            { label: "Usage", slug: "guides/usage" },
            { label: "Architecture", slug: "guides/architecture" },
            { label: "Mirroring & Scheduling", slug: "guides/mirroring" },
          ],
        },
      ],
    }),
  ],
});
