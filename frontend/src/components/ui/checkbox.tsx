import { forwardRef, type InputHTMLAttributes } from "react";
import { Check } from "lucide-react";
import { cn } from "../../lib/utils";

export const Checkbox = forwardRef<HTMLInputElement, Omit<InputHTMLAttributes<HTMLInputElement>, "type">>(
  ({ className, ...props }, ref) => (
    <span className={cn("relative inline-flex size-5 shrink-0", className)}>
      <input
        ref={ref}
        type="checkbox"
        className="peer size-5 appearance-none rounded-ui-sm border border-input bg-background transition-colors duration-normal checked:border-primary checked:bg-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
        {...props}
      />
      <Check className="pointer-events-none absolute inset-0 m-auto size-3.5 text-primary-foreground opacity-0 peer-checked:opacity-100" aria-hidden="true" />
    </span>
  ),
);
Checkbox.displayName = "Checkbox";
