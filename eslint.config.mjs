// valtracker/static のスクリプト用の ESLint（npm run lint）。レイアウトは Prettier（npm run format）、
// ルールはミスを拾う。rules ブロックは News Site と Infra Game のものと同じ。
import js from "@eslint/js";
import globals from "globals";

export default [
  // この PC のデータと、リポジトリの中に作った Python の仮想環境（.venv。中に werkzeug などの JS がある）は lint しない。
  { ignores: ["instance/", ".venv/"] },
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
      // VT: スクリプトが共有する名前空間（window.VT）。
      globals: { ...globals.browser, VT: "readonly" },
    },
  },
  {
    // レイアウトの確認: Node と、ページの中で動く関数 1 つ（inspect）。
    files: ["tests/ui/**/*.js"],
    languageOptions: {
      sourceType: "commonjs",
      globals: { ...globals.node, ...globals.browser },
    },
  },
];
