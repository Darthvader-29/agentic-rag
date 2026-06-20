/**
 * True when the scroll viewport is within `threshold` px of the bottom — i.e. the user is
 * "stuck" to the latest content. Used to decide whether a streamed token should auto-scroll:
 * we follow the stream only while the user is at the bottom, so scrolling up to read earlier
 * messages is no longer hijacked back down on every token (B24).
 */
export function isNearBottom(
  el: Pick<HTMLElement, "scrollHeight" | "scrollTop" | "clientHeight">,
  threshold = 80
): boolean {
  return el.scrollHeight - el.scrollTop - el.clientHeight <= threshold;
}
