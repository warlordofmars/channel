import { defineConfig } from "vitepress";

export default defineConfig({
  base: "/docs/",
  title: "Channel Docs",
  description: "Documentation for Channel",
  cleanUrls: true,
  sitemap: {
    hostname: "https://example.com",
    transformItems: (items) =>
      items.map((item) => ({
        ...item,
        url: `docs/${item.url.replace(/^\//, "")}`,
      })),
  },
  head: [
    ["link", { rel: "icon", type: "image/svg+xml", href: "/docs/favicon.svg" }],
    ["meta", { property: "og:type", content: "website" }],
    ["meta", { property: "og:site_name", content: "Channel" }],
    ["meta", { property: "og:title", content: "Channel Docs" }],
    [
      "meta",
      {
        property: "og:description",
        content: "Documentation for Channel.",
      },
    ],
  ],

  themeConfig: {
    logo: { src: "/logo.svg", alt: "Channel" },
    siteTitle: "Channel",
    // logoLink goes to the marketing page root, not /docs/.
    logoLink: "/",
    // Nav links are rendered via nav-bar-content-after as plain <a> elements.
    nav: [],

    sidebar: [
      {
        text: "Getting started",
        items: [
          { text: "Introduction", link: "/getting-started/introduction" },
          { text: "Quick start", link: "/getting-started/quick-start" },
        ],
      },
      {
        text: "Building agents",
        items: [
          { text: "Overview", link: "/agents/overview" },
          { text: "Sessions", link: "/agents/sessions" },
        ],
      },
      {
        text: "Operations",
        items: [
          { text: "Security and secrets", link: "/operations/security" },
          { text: "Operational endpoints", link: "/operations/endpoints" },
        ],
      },
    ],

    socialLinks: [],
    appearance: true,

    footer: {
      message: "Channel",
    },

    search: {
      provider: "local",
    },
  },
});
