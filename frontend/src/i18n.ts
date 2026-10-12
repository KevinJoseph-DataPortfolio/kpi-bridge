import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import de from "../../shared/locales/de.json";
import en from "../../shared/locales/en.json";
import es from "../../shared/locales/es.json";
import fr from "../../shared/locales/fr.json";
import ru from "../../shared/locales/ru.json";
import zh from "../../shared/locales/zh-Hans.json";

export const LANGUAGES = [
  { code: "fr", label: "Français" },
  { code: "en", label: "English" },
  { code: "ru", label: "Русский" },
  { code: "zh-Hans", label: "简体中文" },
  { code: "de", label: "Deutsch" },
  { code: "es", label: "Español" },
] as const;

const KEY = "kpi-bridge.lang";

function initialLang(): string {
  try {
    const saved = localStorage.getItem(KEY);
    if (saved && LANGUAGES.some((l) => l.code === saved)) return saved;
  } catch {
    /* stockage indisponible */
  }
  return "fr";
}

void i18n.use(initReactI18next).init({
  resources: {
    fr: { translation: fr },
    en: { translation: en },
    ru: { translation: ru },
    "zh-Hans": { translation: zh },
    de: { translation: de },
    es: { translation: es },
  },
  lng: initialLang(),
  fallbackLng: "en",
  interpolation: { escapeValue: false }, // React already escapes all rendered text
  returnNull: false,
});

export function setLanguage(code: string): void {
  void i18n.changeLanguage(code);
  try {
    localStorage.setItem(KEY, code);
  } catch {
    /* ignore */
  }
  document.documentElement.lang = code;
}

document.documentElement.lang = i18n.language;

export default i18n;
