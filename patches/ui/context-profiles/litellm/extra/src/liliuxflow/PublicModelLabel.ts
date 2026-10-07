/** Exact public-model presentation only. Never apply to payloads, IDs, prompts,
 * filters, aggregate keys or stored records. Unknown/user-defined values stay raw. */
export const CURRENT_PUBLIC_MODEL = "qwen3.8-flash-next-lily-q4-64k";
export function publicModelLabel<T>(value: T): T | string {
  if (value === "qwen38-flash-next-q4-safe64k" || value === "openai/qwen38-flash-next-q4-safe64k") return CURRENT_PUBLIC_MODEL;
  return value;
}
