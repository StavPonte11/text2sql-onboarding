import { useTranslation } from "react-i18next";
import { Globe } from "lucide-react";
import { useAppStore } from "../../store/appStore";

export function ScopeBanner() {
  const { t } = useTranslation();
  const { activeScope } = useAppStore();

  if (!activeScope) return null;

  return (
    <div className="scope-banner">
      <Globe size={13} />
      {t("scopes.activeBanner", { name: activeScope.name })}
    </div>
  );
}
