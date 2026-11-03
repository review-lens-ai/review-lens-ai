// PageHeader (ui-ux-standards.md §3, §8): the H1 on the left, the page's single
// primary action on the right.
export function PageHeader({ title, action }: { title: string; action?: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-4">
      <h1 className="text-3xl font-semibold tracking-tight">{title}</h1>
      {action}
    </div>
  );
}
