/** Make each claim id a link to the claim's page, in a plain id or one the model wrote as `code`, and leave the rest of the markdown alone. */
export function linkClaims(text: string): string {
  return text
    .replace(/`(CLM-\d{4})`/g, '[$1](/claims/$1)')
    .split(/(`[^`]*`|\[[^\]]*\]\([^)]*\))/) // code spans and links are left as they are
    .map((part, i) => (i % 2 === 1 ? part : part.replace(/(?<![\w/-])(CLM-\d{4})(?![\w-])/g, '[$1](/claims/$1)')))
    .join('')
}
