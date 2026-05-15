const SESSION_SETTINGS_KEY = 'pdf-reader:representation-settings';
const COOKIE_SETTINGS_KEY = 'pdf_reader_representation_settings';
const COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24 * 365;
const DEFAULT_KEYWORDS_BACKGROUND = '#7a4a12';
const DEFAULT_SUMMARY_BACKGROUND = '#263238';
const DEFAULT_CUSTOM_BACKGROUND = '#24543a';
const DEFAULT_BACKGROUND_OPACITY = 1;
const LEGACY_DEFAULTS = {
  keywords: { background_color: '#f5c15f', background_opacity: 0.6 },
  summary: { background_color: '#fff8eb', background_opacity: 0.6 },
};

export const DEFAULT_REPRESENTATION_SETTINGS = [
  {
    id: 'keywords',
    name: 'keywords',
    prompt:
      "Extract concise, specific noun phrases from the paragraph text. Use the text's own terminology and avoid generic UI or process labels.",
    background_color: DEFAULT_KEYWORDS_BACKGROUND,
    background_opacity: DEFAULT_BACKGROUND_OPACITY,
    enabled: true,
    isDefault: true,
  },
  {
    id: 'summary',
    name: 'summary',
    prompt:
      "Write one concise summary of the paragraph's main claim or finding. Use only the supplied paragraph text.",
    background_color: DEFAULT_SUMMARY_BACKGROUND,
    background_opacity: DEFAULT_BACKGROUND_OPACITY,
    enabled: true,
    isDefault: true,
  },
];

export function resetRepresentationSettings() {
  return DEFAULT_REPRESENTATION_SETTINGS.map((setting) => ({ ...setting }));
}

export function normalizeRepresentationSettings(settings) {
  if (!Array.isArray(settings) || !settings.length) {
    return resetRepresentationSettings();
  }

  return settings.map((setting, index) => {
    const fallback = DEFAULT_REPRESENTATION_SETTINGS[index] ?? {};
    const migratedSetting = migrateLegacyDefault(setting, fallback);
    return {
      id: String(migratedSetting?.id || fallback.id || `custom-${index}`),
      name: String(migratedSetting?.name || fallback.name || 'representation'),
      prompt: String(migratedSetting?.prompt || fallback.prompt || 'Write a concise representation of this paragraph.'),
      background_color: String(migratedSetting?.background_color || fallback.background_color || DEFAULT_CUSTOM_BACKGROUND),
      background_opacity: normalizeOpacity(migratedSetting?.background_opacity ?? fallback.background_opacity ?? DEFAULT_BACKGROUND_OPACITY),
      enabled: Boolean(migratedSetting?.enabled ?? true),
      isDefault: Boolean(migratedSetting?.isDefault ?? fallback.isDefault ?? false),
    };
  });
}

export function defaultCustomRepresentationSettings() {
  return {
    background_color: DEFAULT_CUSTOM_BACKGROUND,
    background_opacity: DEFAULT_BACKGROUND_OPACITY,
  };
}

export function readSessionRepresentationSettings() {
  const raw = window.sessionStorage.getItem(SESSION_SETTINGS_KEY);
  if (!raw) {
    return resetRepresentationSettings();
  }

  try {
    return normalizeRepresentationSettings(JSON.parse(raw));
  } catch {
    window.sessionStorage.removeItem(SESSION_SETTINGS_KEY);
    return resetRepresentationSettings();
  }
}

export function writeSessionRepresentationSettings(settings) {
  window.sessionStorage.setItem(
    SESSION_SETTINGS_KEY,
    JSON.stringify(normalizeRepresentationSettings(settings)),
  );
}

export function toRepresentationDefinitions(settings) {
  if (Array.isArray(settings) && !settings.length) {
    return [];
  }
  return normalizeRepresentationSettings(settings).map((setting) => ({
    name: setting.name.trim(),
    prompt: setting.prompt.trim(),
    background_color: setting.background_color,
    background_opacity: setting.background_opacity,
    enabled: setting.enabled,
  }));
}

function normalizeOpacity(value) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) {
    return DEFAULT_BACKGROUND_OPACITY;
  }
  return Math.min(Math.max(parsed, 0), 1);
}

function migrateLegacyDefault(setting, fallback) {
  const key = String(setting?.id || fallback?.id || setting?.name || fallback?.name || '').toLowerCase();
  const legacy = LEGACY_DEFAULTS[key];
  if (!setting?.isDefault && !fallback?.isDefault && !legacy) {
    return setting;
  }

  if (!legacy) {
    return {
      ...setting,
      background_opacity: setting?.background_opacity ?? DEFAULT_BACKGROUND_OPACITY,
    };
  }

  const migrated = { ...setting };
  if (String(setting?.background_color || '').toLowerCase() === legacy.background_color) {
    migrated.background_color = fallback.background_color;
  }
  if (Number(setting?.background_opacity) === legacy.background_opacity) {
    migrated.background_opacity = fallback.background_opacity;
  }
  if (migrated.background_opacity == null) {
    migrated.background_opacity = DEFAULT_BACKGROUND_OPACITY;
  }
  return migrated;
}

function findCookieValue(name) {
  const prefix = `${name}=`;
  return document.cookie
    .split(';')
    .map((cookie) => cookie.trim())
    .find((cookie) => cookie.startsWith(prefix))
    ?.slice(prefix.length);
}

export function readCookieRepresentationSettings() {
  const raw = findCookieValue(COOKIE_SETTINGS_KEY);
  if (!raw) {
    return null;
  }

  try {
    return normalizeRepresentationSettings(JSON.parse(decodeURIComponent(raw)));
  } catch {
    return null;
  }
}

export function saveCookieRepresentationSettings(settings) {
  const value = encodeURIComponent(JSON.stringify(normalizeRepresentationSettings(settings)));
  document.cookie = `${COOKIE_SETTINGS_KEY}=${value}; Max-Age=${COOKIE_MAX_AGE_SECONDS}; Path=/; SameSite=Lax`;
}

export function clearCookieRepresentationSettings() {
  document.cookie = `${COOKIE_SETTINGS_KEY}=; Max-Age=0; Path=/; SameSite=Lax`;
}
