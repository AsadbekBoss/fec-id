import {
  Building,
  Building2,
  Camera,
  CalendarCheck,
  Clock,
  FileBarChart,
  History,
  ScanFace,
  LayoutDashboard,
  Palette,
  Palmtree,
  Settings,
  Activity,
  UserCog,
  Users,
  type LucideIcon,
} from "lucide-react";
import { t } from "@/lib/i18n";

export type NavItem = {
  href: string;
  label: string;
  icon: LucideIcon;
  /** Permission needed to SEE the item (the server still enforces access). */
  permission?: string;
  devOnly?: boolean;
  /** Active only on this exact path (not on sub-paths that have their own item). */
  exact?: boolean;
};

export function isActive(item: NavItem, pathname: string): boolean {
  return pathname === item.href || (!item.exact && pathname.startsWith(`${item.href}/`));
}

export type NavSection = { title: string; items: NavItem[] };

export const NAV: NavSection[] = [
  {
    title: t.nav.sectionMain,
    items: [
      { href: "/dashboard", label: t.nav.dashboard, icon: LayoutDashboard },
      { href: "/attendance", label: t.nav.attendance, icon: CalendarCheck, permission: "attendance:read", exact: true },
      { href: "/attendance/history", label: t.nav.attendanceHistory, icon: History, permission: "attendance:read" },
      { href: "/employees", label: t.nav.employees, icon: Users, permission: "employees:read" },
      { href: "/recognition", label: t.nav.recognition, icon: ScanFace, permission: "attendance:read" },
    ],
  },
  {
    title: t.nav.sectionOrg,
    items: [
      { href: "/departments", label: t.nav.departments, icon: Building2, permission: "employees:read" },
      { href: "/schedules", label: t.nav.schedules, icon: Clock, permission: "attendance:read" },
      { href: "/leaves", label: t.nav.leaves, icon: Palmtree, permission: "attendance:read" },
      { href: "/cameras", label: t.nav.cameras, icon: Camera, permission: "cameras:read" },
      { href: "/reports", label: t.nav.reports, icon: FileBarChart, permission: "reports:read" },
    ],
  },
  {
    title: t.nav.sectionSystem,
    items: [
      { href: "/branches", label: t.nav.branches, icon: Building, permission: "branches:manage" },
      { href: "/users", label: t.nav.users, icon: UserCog, permission: "users:read" },
      { href: "/settings", label: t.nav.settings, icon: Settings, permission: "settings:read" },
      { href: "/system", label: t.nav.system, icon: Activity, permission: "branches:manage" },
      { href: "/design-system", label: t.nav.designSystem, icon: Palette, devOnly: true },
    ],
  },
];

export function visibleNav(can: (p: string) => boolean, isDev: boolean): NavSection[] {
  return NAV.map((s) => ({
    ...s,
    items: s.items.filter((i) => (!i.permission || can(i.permission)) && (!i.devOnly || isDev)),
  })).filter((s) => s.items.length > 0);
}

export function findNavItem(pathname: string): NavItem | undefined {
  const items = NAV.flatMap((s) => s.items);
  return items.find((i) => pathname === i.href) ?? items.find((i) => isActive(i, pathname));
}
