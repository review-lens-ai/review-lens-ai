import { AppShell } from "@/app/_components/app-shell";

export default function TargetsLayout({ children }: LayoutProps<"/targets">) {
  return <AppShell>{children}</AppShell>;
}
