// ESLint for the scripts in valtracker/static (npm run lint). Prettier owns the layout (npm run format); these rules
// catch mistakes. The rules block is the same as News Site's and Infra Game's.
import js from "@eslint/js";
import globals from "globals";

export default [
  // This PC's data is not ours to lint.
  { ignores: ["instance/"] },
  js.configs.recommended,
  {
    rules: {
      // An unused catch binding, or an argument kept for a shared signature such as (argv, ctx), is fine.
      "no-unused-vars": ["warn", { args: "none", caughtErrors: "none" }],
      "no-useless-assignment": "warn",
      "no-useless-escape": "warn",
      "preserve-caught-error": "warn",
      // Japanese text has full-width spaces.
      "no-irregular-whitespace": ["error", { skipStrings: true, skipTemplates: true, skipRegExps: true }],
      // el.textContent = el.textContent and the like go through a setter on purpose.
      "no-self-assign": ["error", { props: false }],
    },
  },
  {
    files: ["valtracker/static/**/*.js"],
    languageOptions: {
      sourceType: "script",
      // VT: the namespace the scripts share (window.VT).
      globals: { ...globals.browser, VT: "readonly" },
    },
  },
];
