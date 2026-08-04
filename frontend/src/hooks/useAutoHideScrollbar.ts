import { useCallback, useEffect, useRef, useState } from "react";

export function useAutoHideScrollbar<T extends HTMLElement>() {
  const ref = useRef<T | null>(null);
  const timerRef = useRef<number | null>(null);
  const [visible, setVisible] = useState(false);

  const scheduleHide = useCallback((delay = 900) => {
    if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    timerRef.current = window.setTimeout(() => setVisible(false), delay);
  }, []);

  const reveal = useCallback(() => {
    setVisible(true);
    scheduleHide();
  }, [scheduleHide]);

  useEffect(() => {
    return () => {
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    };
  }, []);

  return {
    ref,
    className: `scrollbar-auto-hide${visible ? " scrollbar-visible" : ""}`,
    interactionProps: {
      onScroll: reveal,
      onMouseEnter: reveal,
      onMouseMove: reveal,
      onMouseLeave: () => scheduleHide(250),
      onFocus: reveal,
    },
  };
}
