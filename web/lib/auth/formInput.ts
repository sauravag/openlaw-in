import "server-only";

import { normalizeLanguage, type AuthLanguage } from "@/lib/auth/authRefusal";

const FORM_CONTENT_TYPE = "application/x-www-form-urlencoded";

/** The fields a route asked for, plus the normalised language. */
export interface FormInput<Field extends string> {
  readonly fields: Readonly<Record<Field, unknown>>;
  readonly language: AuthLanguage;
}

/** A no-JavaScript form post must land on a page, never on a JSON body. */
export function isFormPost(request: Request): boolean {
  return (request.headers.get("content-type") ?? "").includes(FORM_CONTENT_TYPE);
}

/** Language for a refusal raised before the body is read (limiter, size cap). */
export function hintLanguage(request: Request): AuthLanguage {
  const accept = request.headers.get("accept-language")?.trim().toLowerCase() ?? "";
  return accept.startsWith("hi") ? "hi" : "en";
}

/**
 * `names` and `language` from a form body or a JSON object; null when the
 * JSON is unparseable or not an object. Values are returned unvalidated:
 * each route validates its own fields.
 */
export function parseFormInput<Field extends string>(
  text: string,
  contentType: string,
  names: readonly Field[],
): FormInput<Field> | null {
  let read: (name: string) => unknown;
  if (contentType.includes(FORM_CONTENT_TYPE)) {
    const form = new URLSearchParams(text);
    read = (name) => form.get(name);
  } else {
    let data: unknown;
    try {
      data = JSON.parse(text);
    } catch {
      return null;
    }
    if (typeof data !== "object" || data === null) return null;
    const record = data as Record<string, unknown>;
    read = (name) => record[name];
  }
  const fields = Object.fromEntries(names.map((name) => [name, read(name)])) as Record<Field, unknown>;
  return { fields, language: normalizeLanguage(read("language")) };
}
