import js from "@eslint/js";
import tsEslintPlugin from "@typescript-eslint/eslint-plugin";
import tsParser from "@typescript-eslint/parser";
import astro from "eslint-plugin-astro";
import globals from "globals";

const bugFinderRules = {
  eqeqeq: ["error", "always", { null: "ignore" }],
  "no-unused-vars": ["error", { argsIgnorePattern: "^_", varsIgnorePattern: "^_" }],
};

const typeAwareLintFiles = [
  "src/utils/season-data.ts",
  "src/utils/load-games.ts",
  "src/layouts/Base.astro",
  "src/pages/index.astro",
  "src/pages/season/[season]/results.astro",
  "src/pages/season/[season]/rankings.astro",
];

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
      parserOptions: {
        project: true,
        tsconfigRootDir: import.meta.dirname,
      },
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
        project: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
  },
  {
    files: typeAwareLintFiles,
    plugins: {
      "@typescript-eslint": tsEslintPlugin,
    },
    rules: {
      "@typescript-eslint/consistent-type-imports": "error",
      "@typescript-eslint/no-explicit-any": "error",
      "@typescript-eslint/no-unsafe-assignment": "error",
      "@typescript-eslint/no-unsafe-member-access": "error",
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
