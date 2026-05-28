# Monorepo Linting, Formatting & Code Quality Developer Guide

This repository uses a modern, unified, high-performance linting, formatting, and type-checking architecture to enforce consistent standards and catch potential errors early.

---

## 1. Tooling Architecture & Justification

### A. Frontend (React / Vite / TypeScript)
* **ESLint (Flat Config, v9):** Analyzes TypeScript and React patterns to enforce code quality, clean lifecycle handling, and prevent common bugs.
* **Prettier:** Opinionated formatter that standardizes syntax styles (quotes, semicolons, spacing, wrapping) globally across TS, JS, CSS, JSON, HTML, and Markdown.
* **`eslint-plugin-simple-import-sort`:** Automatically groups and sorts imports on save according to strict architecture guidelines.
* **`eslint-config-prettier`:** Eliminates conflicts between ESLint rules and Prettier formatting styles.

### B. Backend (Python / FastAPI / SQLModel)
* **Ruff:** Blazingly fast Rust-written replacement for `flake8`, `black`, `isort`, `pyupgrade`, and `autoflake`. It performs:
  * **Linting:** Enforces PEP8, complexity, security analysis, and naming conventions.
  * **Formatting:** Direct substitution for Black.
  * **Import Sorting:** Direct replacement for isort with custom grouping.
* **mypy:** Enforces strict typechecking across FastAPI routers, models, and background services to eliminate runtime type errors.

### C. Shared Automation & IDE Config
* **`.editorconfig`:** Standardizes indentation globally across all IDEs (2 spaces for web assets, 4 spaces for Python).
* **Husky & lint-staged:** Standard Git pre-commit hook that automatically runs linters and formatters only on changed files during a commit.

---

## 2. Strict Import Ordering Rule

All source code files must automatically sort imports at the top of the file in this precise order:

### Frontend JS/TS Import Groups:
1. **React & Standard Library:** Standard built-ins and core packages (e.g. `react`, `react-dom`).
2. **External Packages:** Third-party libraries (e.g. `antd`, `axios`, `lucide-react`).
3. **Internal Modules:** Absolute module paths (e.g. `src/components`, `src/api`).
4. **Relative Imports:** Relative file modules (e.g. `../store`, `./types`).
5. **TS Types:** Explicit TypeScript type imports (`import type { ... }`).
6. **Styles & Assets:** CSS sheets, SVGs, and image assets.

### Backend Python Import Groups:
1. **Standard Library:** Built-in Python modules (e.g. `sys`, `os`, `urllib`).
2. **Third-Party Packages:** Libraries installed in virtual env (e.g. `fastapi`, `sqlmodel`).
3. **First-Party App:** Core application modules (e.g. `app.services`, `app.models`).
4. **Relative Imports:** Local folder files (e.g. `.utils`).

---

## 3. Daily Developer Commands

All standard commands are centralized in the workspace root `package.json` for seamless execution:

### Run Code Formatters (Auto-Format):
```bash
pnpm run format
```
* **Frontend:** Runs `prettier --write` on all web assets.
* **Backend:** Runs `ruff format` on all python files.

### Run Lint Checks & Auto-Fixes:
```bash
# Verify all code styling and rules
pnpm run lint

# Automatically fix all repairable issues & sort imports
pnpm run lint:fix
```
* **Frontend:** Runs `eslint . --fix`.
* **Backend:** Runs `ruff check --fix` and resolves all import order and pep8 style violations.

### Run Static Type Checkers:
```bash
pnpm run type-check
```
* **Frontend:** Runs `tsc --noEmit` to verify TS compilation.
* **Backend:** Runs `mypy` to verify Python type safety.

---

## 4. IDE / VSCode Automatic Setup

To activate fully automated styling on save, open this workspace in **VSCode**. The workspace `.vscode/settings.json` is pre-configured to:
* Auto-format web files using **Prettier** on save.
* Auto-format and auto-fix Python files using **Ruff** on save.
* Run ESLint code action fixes on save.

### Recommended Extensions:
1. [Prettier - Code formatter](https://marketplace.visualstudio.com/items?itemName=esbenp.prettier-vscode)
2. [Ruff (by Astral)](https://marketplace.visualstudio.com/items?itemName=charliermarsh.ruff)
3. [ESLint (by Microsoft)](https://marketplace.visualstudio.com/items?itemName=dbaeumer.vscode-eslint)

---

## 5. Git Pre-Commit Hooks (Husky)

When you run `pnpm install` in this workspace, Husky is automatically initialized.

Every time you execute `git commit`, `lint-staged` will run in the background to automatically:
* Format and lint-fix all changed frontend files.
* Format, sort, and lint-fix all changed backend Python files.

This guarantees that unformatted or broken code can never be committed to the repository.

To bypass the pre-commit checks in urgent scenarios (e.g., local debugging/tests), append the bypass flag:
```bash
git commit -m "work in progress" --no-verify
```

---

## 6. CI/CD Pipeline Commands

To run validation checks in a CI/CD environment, run the following fast, deterministic validation scripts:

```bash
# 1. Format Check (Non-modifying validation)
pnpm run format

# 2. Complete Linter Verification
pnpm run lint

# 3. Complete Type Safety Check
pnpm run type-check
```
If any command exits with a non-zero status code, the CI build should fail.
