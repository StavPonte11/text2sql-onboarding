import js from '@eslint/js';
import globals from 'globals';
import reactHooks from 'eslint-plugin-react-hooks';
import reactRefresh from 'eslint-plugin-react-refresh';
import tseslint from 'typescript-eslint';
import simpleImportSort from 'eslint-plugin-simple-import-sort';
import eslintConfigPrettier from 'eslint-config-prettier';

export default tseslint.config(
  { ignores: ['dist', 'src/api/schema.d.ts'] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  eslintConfigPrettier,
  {
    files: ['**/*.{ts,tsx}'],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
    },
    plugins: {
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
      'simple-import-sort': simpleImportSort,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],
      '@typescript-eslint/no-explicit-any': 'warn',
      '@typescript-eslint/no-unused-vars': ['warn', { argsIgnorePattern: '^_' }],
      'no-unused-vars': 'off',
      'simple-import-sort/imports': [
        'error',
        {
          groups: [
            // 1. Standard library and react-related, external packages
            ['^react', '^@?\\w'],
            // 2. Internal modules / components
            ['^src/'],
            // 3. Relative imports
            ['^\\.\\.(?!/?$)', '^\\.\\./?$', '^\\./(?=.*/)(?!/?$)', '^\\./?$', '^\\./[^.]'],
            // 4. TS Types
            ['^.*\\u0000$'],
            // 5. Styles & assets
            ['^.+\\.(css|scss|sass|less)$', '^.+\\.(png|jpg|jpeg|gif|svg|webp)$'],
          ],
        },
      ],
      'simple-import-sort/exports': 'error',
    },
  },
);
