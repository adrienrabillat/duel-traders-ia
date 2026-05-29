import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ['"Inter"', "system-ui", "sans-serif"],
      },
      colors: {
        ink: "#0B0B0F",
        muted: "#6B7280",
        accent: "#7C5CFF",
        positive: "#10B981",
        negative: "#EF4444",
        card: "#FFFFFF",
        surface: "#F8F8FA",
      },
      borderRadius: {
        xl: "14px",
        "2xl": "20px",
      },
      boxShadow: {
        card: "0 1px 2px rgba(11,11,15,0.04), 0 4px 16px rgba(11,11,15,0.04)",
      },
    },
  },
  plugins: [],
};

export default config;
