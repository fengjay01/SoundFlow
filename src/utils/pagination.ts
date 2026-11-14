export const MODEL_LIBRARY_PAGE_SIZES = [12, 24, 48, 96] as const
export const SEPARATE_MODEL_PAGE_SIZES = [8, 12, 24] as const

export function normalizePageSize(
  value: unknown,
  allowed: readonly number[],
  fallback: number,
) {
  return typeof value === 'number' && Number.isInteger(value) && allowed.includes(value)
    ? value
    : fallback
}
