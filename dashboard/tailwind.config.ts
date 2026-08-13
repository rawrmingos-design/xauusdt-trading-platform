import type { Config } from "tailwindcss";

export default {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#0b0f14",
        panel: "#11161d",
        edge: "#1e2630",
        accent: "#4cc38a",
        warn: "#e2b93b",
        danger: "#e5534b",
      },
    },
  },
  plugins: [],
} satisfies Config;
