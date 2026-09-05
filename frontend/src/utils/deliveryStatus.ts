/** Only fully accepted mathematical and document deliveries show as accepted. */
export function isAcceptedDelivery(status?: string): boolean {
  return status === 'completed' || status === 'completed_with_warnings';
}
