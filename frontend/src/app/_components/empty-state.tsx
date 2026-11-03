// EmptyState (ui-ux-standards.md §7.2, §8): every empty list or panel gets a message
// and a next action. Never placeholder data.
export function EmptyState({
  icon,
  title,
  message,
  action,
}: {
  icon: React.ReactNode;
  title?: string;
  message: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col items-center gap-3 rounded-xl border border-dashed bg-card px-6 py-12 text-center">
      <span className="text-muted-foreground [&_svg]:size-8" aria-hidden="true">
        {icon}
      </span>
      {title && <h2 className="text-lg font-semibold">{title}</h2>}
      <p className="max-w-md text-sm text-muted-foreground">{message}</p>
      {action}
    </div>
  );
}
