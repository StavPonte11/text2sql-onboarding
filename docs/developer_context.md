# 🤖 AI Agent & Developer Onboarding Context

Welcome to the **TextToSQL Studio** workspace. Use this reference document to quickly understand current conventions and safely implement state migrations.

## 🛠 Project Foundations

- **Framework Context**: FastAPI handles backend tasks effortlessly via straightforward ORM mappings.
- **Rules of Conduct**:
  - Keep modules split neatly between `routers` and `models`.
  - Prefer declarative state changes across the application UI components.

## 🎨 UX/UI & Frontend Guidelines (Vite/React)

We rely on **Vite** and **React (TypeScript)** for a fast, type-safe frontend experience.

- **Component Styling**: Use **Tailwind CSS v4** for rapid UI development.
- **State Management**: Keep state local to components or use React Context for simple global needs. Avoid heavy external state libraries unless explicitly required.
- **Routing**: Use **React Router** for navigation.
- **File Structure**: Organize components logically within `src/components/` and `src/pages/`. Keep related code together (e.g., `Modal.tsx` and `Modal.css`).

### Frontend Agent Directive
> Ensure all UI changes follow the responsive grid layout and use Tailwind utility classes for styling. Verify component interactions match the state management patterns defined in `src/store/`.
