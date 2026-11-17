"use client";

// AppShell (ui-ux-standards.md §2, §3, §8): top bar + sidebar + content, adapted from
// the shadcn sidebar-16 block. The target list comes from useTargets (lib/hooks).
import { Suspense } from "react";
import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { ChevronDownIcon, LogOutIcon, PlusIcon, SearchIcon } from "lucide-react";
import { Avatar, AvatarFallback } from "@/app/_components/ui/avatar";
import { Button } from "@/app/_components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/app/_components/ui/dropdown-menu";
import {
  Sidebar,
  SidebarContent,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarInset,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarMenuSkeleton,
  SidebarProvider,
  SidebarTrigger,
  useSidebar,
} from "@/app/_components/ui/sidebar";
import { statusDotClass, statusLabel } from "@/app/_components/status-badge";
import { useReadMe } from "@/lib/generated/api";
import { useTargets } from "@/lib/hooks/use-targets";
import { createClient } from "@/lib/supabase/client";

export function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="[--header-height:calc(--spacing(14))]">
      <SidebarProvider
        className="flex flex-col"
        style={{ "--sidebar-width": "15rem" } as React.CSSProperties}
      >
        <TopBar />
        <div className="flex flex-1">
          <AppSidebar />
          <SidebarInset>
            <div className="w-full max-w-[1200px] p-4 lg:p-8">{children}</div>
          </SidebarInset>
        </div>
      </SidebarProvider>
    </div>
  );
}

function TopBar() {
  return (
    <header className="sticky top-0 z-50 flex w-full items-center border-b bg-card">
      <div className="flex h-(--header-height) w-full items-center gap-2 px-4 lg:px-6">
        <SidebarTrigger className="lg:hidden" aria-label="Toggle navigation" />
        <Link href="/targets" className="flex items-center gap-2 font-semibold">
          <SearchIcon className="size-5 text-primary" aria-hidden="true" />
          ReviewLens
        </Link>
        <div className="ml-auto">
          <UserMenu />
        </div>
      </div>
    </header>
  );
}

function UserMenu() {
  const router = useRouter();
  const queryClient = useQueryClient();
  // The menu still works if this fails: it just shows a generic name.
  const { data: me } = useReadMe();

  async function signOut() {
    await createClient().auth.signOut();
    // Forget this user's cached data before anyone else signs in on this browser.
    queryClient.clear();
    router.replace("/login");
    router.refresh();
  }

  const name = me?.email || "Account";

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" className="gap-2 px-2">
          <Avatar className="size-6">
            <AvatarFallback className="bg-sidebar-accent text-xs font-medium text-primary">
              {name.charAt(0).toUpperCase()}
            </AvatarFallback>
          </Avatar>
          <span className="hidden max-w-48 truncate sm:inline">{name}</span>
          <ChevronDownIcon className="size-4 text-muted-foreground" aria-hidden="true" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-56">
        <DropdownMenuLabel className="truncate font-normal text-muted-foreground">
          {name}
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={signOut}>
          <LogOutIcon aria-hidden="true" />
          Sign out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function AppSidebar() {
  const { setOpenMobile } = useSidebar();

  return (
    <Sidebar className="top-(--header-height) h-[calc(100svh-var(--header-height))]!">
      <SidebarHeader className="p-4">
        <Button asChild className="w-full">
          <Link href="/targets/new" onClick={() => setOpenMobile(false)}>
            <PlusIcon aria-hidden="true" />
            New target
          </Link>
        </Button>
      </SidebarHeader>
      <SidebarContent>
        <SidebarGroup className="px-3">
          <SidebarGroupLabel>My targets</SidebarGroupLabel>
          <SidebarGroupContent>
            {/* usePathname reads the URL at runtime, so it needs a Suspense boundary. */}
            <Suspense fallback={<TargetListSkeleton />}>
              <TargetNav />
            </Suspense>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>
    </Sidebar>
  );
}

function TargetListSkeleton() {
  return (
    <SidebarMenu aria-label="Loading your targets">
      {[0, 1, 2].map((i) => (
        <SidebarMenuItem key={i}>
          <SidebarMenuSkeleton />
        </SidebarMenuItem>
      ))}
    </SidebarMenu>
  );
}

function TargetNav() {
  const pathname = usePathname();
  const { setOpenMobile } = useSidebar();
  const { targets, error } = useTargets();

  if (error) {
    return <p className="px-2 text-sm text-muted-foreground">Your targets couldn&apos;t load.</p>;
  }
  if (targets === null) return <TargetListSkeleton />;
  if (targets.length === 0) {
    return <p className="px-2 text-sm text-muted-foreground">No targets yet.</p>;
  }

  return (
    <SidebarMenu>
      {targets.map((target) => {
        const href = `/targets/${target.id}`;
        return (
          <SidebarMenuItem key={target.id}>
            <SidebarMenuButton asChild size="lg" isActive={pathname === href}>
              <Link href={href} onClick={() => setOpenMobile(false)}>
                <span className="flex min-w-0 flex-1 flex-col">
                  <span className="truncate font-medium">{target.name}</span>
                  <span className="truncate text-xs font-normal text-muted-foreground">
                    {statusLabel(target.ingestion_status)}
                  </span>
                </span>
                <span
                  aria-hidden="true"
                  className={`size-2 shrink-0 rounded-full ${statusDotClass(target.ingestion_status)}`}
                />
              </Link>
            </SidebarMenuButton>
          </SidebarMenuItem>
        );
      })}
    </SidebarMenu>
  );
}
