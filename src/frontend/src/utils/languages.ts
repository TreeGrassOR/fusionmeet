// Map frontend language codes to backend language codes

export type BackendLanguage =
  | 'ru-ru'
  | 'en-us'
  | 'fr-fr'
  | 'nl-nl'
  | 'de-de'
  | 'es-es'
export type FrontendLanguage = 'ru' | 'en' | 'fr' | 'nl' | 'de' | 'es'

const frontendToBackendMap: Record<FrontendLanguage, BackendLanguage> = {
  ru: 'ru-ru',
  en: 'en-us',
  fr: 'fr-fr',
  nl: 'nl-nl',
  de: 'de-de',
  es: 'es-es',
}

export const convertToBackendLanguage = (
  frontendLang: string = 'ru'
): BackendLanguage => {
  return frontendToBackendMap[frontendLang as FrontendLanguage]
}
