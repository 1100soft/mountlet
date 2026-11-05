import type { Remote } from "./model.ts";

export function isGoogle(remote: Remote): boolean {
  return remote.provider === "drive" || remote.provider === "gphotos";
}

export function sameRemoteCard(a: Remote | undefined, b: Remote | undefined): boolean {
  return Boolean(a && b && (a.id === b.id || (isGoogle(a) && isGoogle(b) && a.googleGroup && a.googleGroup === b.googleGroup)));
}

/** Only persisted links collapse rows; account matching belongs to the backend. */
export function googleCards(remotes: readonly Remote[]): readonly Remote[] {
  const cards: Remote[] = [];
  for (const remote of remotes) {
    const index = cards.findIndex(card => sameRemoteCard(card, remote));
    if (index < 0) cards.push(remote);
    else if (remote.provider === "drive") cards[index] = remote;
  }
  return cards;
}
