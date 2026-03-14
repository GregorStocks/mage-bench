import js from "@eslint/js";
import tsParser from "@typescript-eslint/parser";
import astro from "eslint-plugin-astro";
import globals from "globals";

const bugFinderRules = {
  eqeqeq: ["error", "always", { null: "ignore" }],
  "no-unused-vars": ["error", { argsIgnorePattern: "^_", varsIgnorePattern: "^_" }],
};

export default [
  {
    ignores: [
      "dist/**",
      "node_modules/**",
      "public/data/**",
      "public/games/**",
      "src/data/**",
      "src/types/game-export.d.ts",
    ],
  },
  js.configs.recommended,
  ...astro.configs.recommended,
  {
    files: ["**/*.{astro,js,mjs,cjs,ts}"],
    rules: bugFinderRules,
  },
  {
    files: ["src/scripts/**/*.{js,ts}"],
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "module",
      globals: {
        ...globals.browser,
        ...globals.commonjs,
      },
    },
  },
  {
    files: ["src/**/*.ts"],
    languageOptions: {
      parser: tsParser,
      ecmaVersion: "latest",
      sourceType: "module",
      globals: {
        ...globals.node,
      },
    },
  },
  {
    files: ["src/**/*.astro"],
    languageOptions: {
      parserOptions: {
        parser: tsParser,
        extraFileExtensions: [".astro"],
      },
    },
  },
  {
    files: ["tests/**/*.js"],
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "module",
      globals: {
        ...globals.browser,
        ...globals.node,
        ...globals.vitest,
      },
    },
  },
  {
    files: ["*.js", "*.mjs"],
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "module",
      globals: {
        ...globals.node,
      },
    },
  },
  {
    files: ["*.cjs"],
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "script",
      globals: {
        ...globals.node,
        ...globals.commonjs,
      },
    },
  },
];
