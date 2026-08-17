import { forwardRef, useId, useLayoutEffect, useRef, useState, type ButtonHTMLAttributes, type FocusEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { cn } from "../../lib/utils";

type IconButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  label: string;
  tooltip?: string;
  children: ReactNode;
};

export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(
  ({ label, tooltip = label, children, className, type = "button", disabled, onFocus, onBlur, ...props }, ref) => {
    const tooltipId = useId();
    const buttonRef = useRef<HTMLButtonElement | null>(null);
    const tooltipRef = useRef<HTMLSpanElement | null>(null);
    const [open, setOpen] = useState(false);
    const [position, setPosition] = useState({ left: 0, top: 0, below: false });

    useLayoutEffect(() => {
      if (!open || !buttonRef.current || !tooltipRef.current) return;
      const updatePosition = () => {
        const rect = buttonRef.current!.getBoundingClientRect();
        const tooltipWidth = tooltipRef.current!.offsetWidth;
        const centeredLeft = rect.left + rect.width / 2 - tooltipWidth / 2;
        const left = Math.min(Math.max(8, centeredLeft), window.innerWidth - tooltipWidth - 8);
        const below = rect.top < 56;
        setPosition({ left, top: below ? rect.bottom + 8 : rect.top - 8, below });
      };
      updatePosition();
      window.addEventListener("resize", updatePosition);
      window.addEventListener("scroll", updatePosition, true);
      return () => {
        window.removeEventListener("resize", updatePosition);
        window.removeEventListener("scroll", updatePosition, true);
      };
    }, [open]);

    const handleBlur = (event: FocusEvent<HTMLSpanElement>) => {
      if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false);
    };
    const setButtonRef = (node: HTMLButtonElement | null) => {
      buttonRef.current = node;
      if (typeof ref === "function") ref(node);
      else if (ref) (ref as { current: HTMLButtonElement | null }).current = node;
    };

    return <>
      <span
        className={disabled ? "relative inline-flex shrink-0" : "contents"}
        tabIndex={disabled ? 0 : undefined}
        aria-label={disabled ? `${label}：${tooltip}` : undefined}
        aria-describedby={disabled ? tooltipId : undefined}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        onFocusCapture={() => setOpen(true)}
        onBlurCapture={handleBlur}
      >
        <button
          ref={setButtonRef}
          type={type}
          aria-label={label}
          aria-describedby={!disabled ? tooltipId : undefined}
          disabled={disabled}
          onFocus={onFocus}
          onBlur={onBlur}
          className={cn(
            "inline-flex size-9 shrink-0 items-center justify-center rounded-ui-md text-muted-foreground transition-colors",
            "hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            "disabled:pointer-events-none disabled:opacity-40",
            className,
          )}
          {...props}
        >
          {children}
        </button>
      </span>
      {open && createPortal(
        <span
          ref={tooltipRef}
          id={tooltipId}
          role="tooltip"
          className="pointer-events-none fixed z-[100] w-max max-w-72 rounded-ui-md border border-border bg-popover px-2.5 py-1.5 text-left text-ui-xs leading-5 text-popover-foreground shadow-overlay"
          style={{
            left: position.left,
            top: position.top,
            transform: position.below ? undefined : "translateY(-100%)",
          }}
        >
          {tooltip}
        </span>,
        document.body,
      )}
    </>;
  },
);
IconButton.displayName = "IconButton";
