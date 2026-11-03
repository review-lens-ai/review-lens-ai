"use client";

import Link from "next/link";
import { ChevronRightIcon, PlusIcon, TargetIcon, TriangleAlertIcon } from "lucide-react";
import { useTargets } from "@/app/_components/app-shell";
import { EmptyState } from "@/app/_components/empty-state";
import { PageHeader } from "@/app/_components/page-header";
import { PlatformBadge } from "@/app/_components/platform-badge";
import { StatusBadge } from "@/app/_components/status-badge";
import { Alert, AlertDescription, AlertTitle } from "@/app/_components/ui/alert";
import { Button } from "@/app/_components/ui/button";
import { Skeleton } from "@/app/_components/ui/skeleton";
import { shortSource } from "@/lib/targets";

function NewTargetButton() {
  return (
    <Button asChild>
      <Link href="/targets/new">
        <PlusIcon aria-hidden="true" />
        New target
      </Link>
    </Button>
  );
}

export default function TargetsPage() {
  const { targets, error } = useTargets();

  return (
    <div className="flex flex-col gap-6">
      <PageHeader title="My targets" action={<NewTargetButton />} />

      {error && (
        <Alert variant="destructive">
          <TriangleAlertIcon aria-hidden="true" />
          <AlertTitle>We couldn&apos;t load your targets.</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {!error && targets === null && (
        <div role="status" aria-label="Loading your targets" className="flex flex-col gap-3">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-22 w-full rounded-xl" />
          ))}
        </div>
      )}

      {targets?.length === 0 && (
        <EmptyState
          icon={<TargetIcon />}
          message="No targets yet. Create one to start analyzing reviews."
          action={<NewTargetButton />}
        />
      )}

      {targets && targets.length > 0 && (
        <ul className="flex flex-col gap-3">
          {targets.map((target) => (
            <li key={target.id}>
              <Link
                href={`/targets/${target.id}`}
                className="flex items-center gap-4 rounded-xl border bg-card p-4 transition-colors hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
              >
                <span className="flex min-w-0 flex-1 flex-col gap-2">
                  <span className="truncate font-semibold">{target.name}</span>
                  <span className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
                    <PlatformBadge platform={target.platform} />
                    <span className="truncate font-mono text-xs">
                      {shortSource(target.source_url)}
                    </span>
                  </span>
                </span>
                <StatusBadge status={target.ingestion_status} />
                <ChevronRightIcon
                  className="size-4 shrink-0 text-muted-foreground"
                  aria-hidden="true"
                />
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
