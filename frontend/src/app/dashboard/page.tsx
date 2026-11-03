import { redirect } from "next/navigation";

// ui-ux-standards.md §2: the signed-in home is the target list.
export default function DashboardPage() {
  redirect("/targets");
}
