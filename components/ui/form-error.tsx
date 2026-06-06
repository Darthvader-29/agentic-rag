/**
 * Inline form error message. Renders an alert-role paragraph styled identically to the
 * hand-rolled error blocks it replaces, and renders nothing when there is no message.
 */
export function FormError({ message }: { message?: string | null }) {
  if (!message) return null;
  return (
    <p role="alert" className="text-destructive text-sm">
      {message}
    </p>
  );
}
