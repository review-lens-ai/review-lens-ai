// PlatformBadge (ui-ux-standards.md §8).
import { MapPinIcon } from "lucide-react";
import { Badge } from "@/app/_components/ui/badge";
import { platformLabel } from "@/lib/targets";

export function PlatformBadge({ platform }: { platform: string }) {
  return (
    <Badge variant="secondary" className="rounded-full font-medium">
      <MapPinIcon aria-hidden="true" />
      {platformLabel(platform)}
    </Badge>
  );
}
