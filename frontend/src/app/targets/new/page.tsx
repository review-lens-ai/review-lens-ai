"use client";

// Create target (ui-ux-standards.md §12.1): the backend validates every field, and its
// field-level errors are shown inline. Nothing is validated only in the browser.
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Loader2Icon, TriangleAlertIcon } from "lucide-react";
import { toast } from "sonner";
import { PageHeader } from "@/app/_components/page-header";
import { PlatformBadge } from "@/app/_components/platform-badge";
import { Alert, AlertTitle } from "@/app/_components/ui/alert";
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "@/app/_components/ui/breadcrumb";
import { Button } from "@/app/_components/ui/button";
import { Card, CardContent } from "@/app/_components/ui/card";
import {
  Field,
  FieldDescription,
  FieldError,
  FieldGroup,
  FieldLabel,
} from "@/app/_components/ui/field";
import { Input } from "@/app/_components/ui/input";
import { apiFetch, ApiError } from "@/lib/api";
import { useTargets } from "@/lib/hooks/use-targets";
import { fixFieldsMessage, SUPPORTED_PLATFORM, SUPPORTED_PLATFORM_LABEL } from "@/lib/targets";
import type { Target } from "@/lib/targets";

type FieldErrors = { name?: string; source_url?: string; form?: string };

function Required() {
  return <span className="font-normal text-muted-foreground">(required)</span>;
}

export default function NewTargetPage() {
  const router = useRouter();
  const { refresh } = useTargets();
  const [name, setName] = useState("");
  const [sourceUrl, setSourceUrl] = useState("");
  const [errors, setErrors] = useState<FieldErrors>({});
  const [submitting, setSubmitting] = useState(false);
  const nameInput = useRef<HTMLInputElement>(null);
  const sourceUrlInput = useRef<HTMLInputElement>(null);

  // §6.2: move keyboard focus to the first invalid field.
  useEffect(() => {
    if (errors.name) nameInput.current?.focus();
    else if (errors.source_url) sourceUrlInput.current?.focus();
  }, [errors]);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setErrors({});
    try {
      const target = await apiFetch<Target>("/api/v1/targets", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, source_url: sourceUrl }),
      });
      await refresh();
      toast.success("Target created.");
      router.push(`/targets/${target.id}`);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        router.replace("/login");
        return;
      }
      if (err instanceof ApiError && err.code === "VALIDATION_ERROR") {
        const next: FieldErrors = {};
        for (const issue of err.details) {
          if (issue.field === "name") next.name = issue.message;
          else if (issue.field === "source_url") next.source_url = issue.message;
          else next.form = issue.message;
        }
        setErrors(next);
      } else {
        setErrors({
          form:
            err instanceof ApiError ? err.message : "Could not reach the server. Please try again.",
        });
      }
    } finally {
      setSubmitting(false);
    }
  }

  const invalidFields = [errors.name, errors.source_url].filter(Boolean).length;

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-2">
        <Breadcrumb>
          <BreadcrumbList>
            <BreadcrumbItem>
              <BreadcrumbLink asChild>
                <Link href="/targets">My targets</Link>
              </BreadcrumbLink>
            </BreadcrumbItem>
            <BreadcrumbSeparator />
            <BreadcrumbItem>
              <BreadcrumbPage>New target</BreadcrumbPage>
            </BreadcrumbItem>
          </BreadcrumbList>
        </Breadcrumb>
        <PageHeader title="New analysis target" />
      </div>

      <Card className="max-w-[560px]">
        <CardContent>
          {/* autoComplete="off" stops Safari refilling the fields after a refresh. */}
          <form onSubmit={onSubmit} noValidate autoComplete="off">
            <FieldGroup className="gap-5">
              {invalidFields > 0 && (
                <Alert variant="destructive" className="border-destructive/30 bg-destructive/10">
                  <TriangleAlertIcon aria-hidden="true" />
                  <AlertTitle>{fixFieldsMessage(invalidFields)}</AlertTitle>
                </Alert>
              )}
              {errors.form && (
                <Alert variant="destructive" className="border-destructive/30 bg-destructive/10">
                  <TriangleAlertIcon aria-hidden="true" />
                  <AlertTitle className="line-clamp-none">{errors.form}</AlertTitle>
                </Alert>
              )}

              <Field data-invalid={Boolean(errors.name)}>
                <FieldLabel htmlFor="target-name">
                  Target name <Required />
                </FieldLabel>
                <FieldDescription id="target-name-help">
                  The business or product you&apos;re analyzing.
                </FieldDescription>
                <Input
                  ref={nameInput}
                  id="target-name"
                  type="text"
                  value={name}
                  disabled={submitting}
                  onChange={(e) => setName(e.target.value)}
                  aria-invalid={Boolean(errors.name)}
                  aria-describedby={
                    errors.name ? "target-name-help target-name-error" : "target-name-help"
                  }
                  placeholder="Joe's Pizza, Newark"
                />
                {errors.name && (
                  <FieldError id="target-name-error">
                    <span className="flex items-center gap-1">
                      <TriangleAlertIcon className="size-4 shrink-0" aria-hidden="true" />
                      {errors.name}
                    </span>
                  </FieldError>
                )}
              </Field>

              <Field>
                <FieldLabel asChild>
                  <span>Review platform</span>
                </FieldLabel>
                <FieldDescription>
                  ReviewLens currently supports {SUPPORTED_PLATFORM_LABEL} only.
                </FieldDescription>
                <div>
                  <PlatformBadge platform={SUPPORTED_PLATFORM} />
                </div>
              </Field>

              <Field data-invalid={Boolean(errors.source_url)}>
                <FieldLabel htmlFor="source-url">
                  Source URL <Required />
                </FieldLabel>
                <Input
                  ref={sourceUrlInput}
                  id="source-url"
                  type="url"
                  inputMode="url"
                  value={sourceUrl}
                  disabled={submitting}
                  onChange={(e) => setSourceUrl(e.target.value)}
                  aria-invalid={Boolean(errors.source_url)}
                  aria-describedby={errors.source_url ? "source-url-error" : undefined}
                  placeholder="https://www.google.com/maps/place/..."
                  className="font-mono"
                />
                {errors.source_url && (
                  <FieldError id="source-url-error">
                    <span className="flex items-center gap-1">
                      <TriangleAlertIcon className="size-4 shrink-0" aria-hidden="true" />
                      {errors.source_url}
                    </span>
                  </FieldError>
                )}
              </Field>

              <div className="flex justify-end gap-3 border-t pt-5">
                {submitting ? (
                  <Button type="button" variant="outline" disabled>
                    Cancel
                  </Button>
                ) : (
                  <Button asChild variant="outline">
                    <Link href="/targets">Cancel</Link>
                  </Button>
                )}
                <Button type="submit" disabled={submitting}>
                  {submitting && <Loader2Icon className="animate-spin" aria-hidden="true" />}
                  {submitting ? "Creating…" : "Create target"}
                </Button>
              </div>
            </FieldGroup>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
