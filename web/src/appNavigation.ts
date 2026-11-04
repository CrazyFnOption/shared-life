/** Local hash routes keep the established PWA scope and offline document unchanged. */
export const appRoutes = ['/home', '/accounting', '/memos', '/plans', '/settings', '/settings/account', '/settings/family', '/settings/notifications'] as const
export type AppRoute = typeof appRoutes[number]
export function parseAppRoute(hash: string): AppRoute {
  const path = hash.replace(/^#/, '')
  return appRoutes.includes(path as AppRoute) ? path as AppRoute : '/home'
}
