import { useEffect, useRef, useState, type RefObject } from "react";

/*
 * Stage 19 (usability): review long assessments without losing
 * navigation context.
 *
 * A sticky bar that always shows which assessment / stage the reviewer
 * is looking at plus an "On this page" jump list built from the <h2>
 * section headings inside `containerRef`. It:
 *  - gives headings stable ids when they don't have one,
 *  - highlights the section currently in view (IntersectionObserver)
 *    via aria-current="location",
 *  - persists the scroll position and active section per `storageKey`
 *    in sessionStorage and restores them once `ready` is true,
 *  - shows a "Back to top" button after the reviewer scrolls down.
 *
 * It only reads the DOM, so the (very large) host component doesn't have
 * to be restructured to use it.
 */

interface SectionLink {
  id: string;
  label: string;
}

interface SectionNavigatorProps {
  containerRef: RefObject<HTMLElement | null>;
  title: string;
  referenceId: string;
  stageLabel: string;
  /** sessionStorage key prefix, unique per assessment + view. */
  storageKey: string;
  /** False while the content is still loading (delays scroll restore). */
  ready: boolean;
  /** Where "Back to top" moves keyboard focus. */
  topTargetId?: string;
}

const BACK_TO_TOP_THRESHOLD = 480;

function slugify(value: string): string {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 40);
}

function readSession(key: string): string | null {
  try {
    return window.sessionStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeSession(key: string, value: string) {
  try {
    window.sessionStorage.setItem(key, value);
  } catch {
    // Storage unavailable (private mode / quota) -- context just won't persist.
  }
}

function sameLinks(a: SectionLink[], b: SectionLink[]): boolean {
  return (
    a.length === b.length &&
    a.every((link, index) => link.id === b[index].id && link.label === b[index].label)
  );
}

export default function SectionNavigator({
  containerRef,
  title,
  referenceId,
  stageLabel,
  storageKey,
  ready,
  topTargetId = "main-content",
}: SectionNavigatorProps) {
  const [sections, setSections] = useState<SectionLink[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [showBackToTop, setShowBackToTop] = useState(false);
  const restoredKeyRef = useRef<string | null>(null);

  // Collect <h2> headings in the container, re-scanning as content loads.
  useEffect(() => {
    const container = containerRef.current;
    if (!container) {
      return;
    }

    let frame = 0;

    const scan = () => {
      const headings = Array.from(container.querySelectorAll<HTMLElement>("h2"));
      const used = new Set<string>();
      const links: SectionLink[] = [];

      headings.forEach((heading, index) => {
        const label = (heading.textContent ?? "").replace(/\s+/g, " ").trim();
        if (!label) {
          return;
        }
        if (!heading.id) {
          heading.id = `section-${slugify(label) || "part"}-${index}`;
        }
        if (used.has(heading.id)) {
          return;
        }
        used.add(heading.id);
        // Lets "jump to section" move keyboard focus to the heading.
        if (!heading.hasAttribute("tabindex")) {
          heading.setAttribute("tabindex", "-1");
        }
        links.push({ id: heading.id, label });
      });

      setSections((current) => (sameLinks(current, links) ? current : links));
    };

    const schedule = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(scan);
    };

    schedule();
    const observer = new MutationObserver(schedule);
    observer.observe(container, { childList: true, subtree: true, characterData: true });

    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
    };
  }, [containerRef, storageKey]);

  // Track which section is in view.
  useEffect(() => {
    if (sections.length === 0 || typeof IntersectionObserver === "undefined") {
      return;
    }

    const visible = new Map<string, number>();
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            visible.set(entry.target.id, entry.boundingClientRect.top);
          } else {
            visible.delete(entry.target.id);
          }
        });

        if (visible.size > 0) {
          const [topmost] = [...visible.entries()].sort((a, b) => a[1] - b[1]);
          setActiveId(topmost[0]);
        }
      },
      // Below the sticky bar, top ~40% of the viewport.
      { rootMargin: "-110px 0px -55% 0px", threshold: 0 }
    );

    sections.forEach((section) => {
      const element = document.getElementById(section.id);
      if (element) {
        observer.observe(element);
      }
    });

    return () => observer.disconnect();
  }, [sections]);

  // Persist the active section.
  useEffect(() => {
    if (activeId) {
      writeSession(`${storageKey}:section`, activeId);
    }
  }, [activeId, storageKey]);

  // Persist scroll position + toggle "Back to top".
  useEffect(() => {
    let frame = 0;

    const onScroll = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const y = window.scrollY;
        setShowBackToTop(y > BACK_TO_TOP_THRESHOLD);
        // Don't overwrite the saved position before it has been restored.
        if (restoredKeyRef.current === storageKey) {
          writeSession(`${storageKey}:scroll`, String(Math.round(y)));
        }
      });
    };

    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("scroll", onScroll);
    };
  }, [storageKey]);

  // Restore scroll position once the content for this key is ready.
  useEffect(() => {
    if (!ready || restoredKeyRef.current === storageKey) {
      return;
    }

    const saved = Number(readSession(`${storageKey}:scroll`));
    const timers: number[] = [];

    const restore = () => {
      if (Number.isFinite(saved) && saved > 0) {
        window.scrollTo({ top: saved });
      }
    };

    // Content (sub-panels) keeps loading for a moment after `ready`, so
    // retry briefly until the page is tall enough.
    timers.push(window.setTimeout(restore, 0));
    timers.push(window.setTimeout(restore, 350));
    timers.push(
      window.setTimeout(() => {
        restore();
        restoredKeyRef.current = storageKey;
      }, 900)
    );

    return () => timers.forEach((timer) => window.clearTimeout(timer));
  }, [ready, storageKey]);

  function jumpTo(id: string) {
    const element = document.getElementById(id);
    if (!element) {
      return;
    }
    element.scrollIntoView({ behavior: "smooth", block: "start" });
    element.focus({ preventScroll: true });
    setActiveId(id);
  }

  function backToTop() {
    window.scrollTo({ top: 0, behavior: "smooth" });
    const target = document.getElementById(topTargetId);
    target?.focus({ preventScroll: true });
  }

  return (
    <>
      <div className="section-nav-bar">
        <div className="section-nav-context" aria-live="polite">
          <strong className="section-nav-title" title={title}>
            {title}
          </strong>
          <span className="section-nav-meta">
            <span>{referenceId}</span>
            <span aria-hidden="true">·</span>
            <span>
              <span className="sr-only">Current view: </span>
              {stageLabel}
            </span>
          </span>
        </div>

        {sections.length > 1 && (
          <nav className="section-nav" aria-label="On this page">
            <span className="section-nav-heading" aria-hidden="true">
              On this page:
            </span>
            <ol>
              {sections.map((section) => (
                <li key={section.id}>
                  <a
                    href={`#${section.id}`}
                    aria-current={activeId === section.id ? "location" : undefined}
                    className={activeId === section.id ? "active" : ""}
                    onClick={(event) => {
                      event.preventDefault();
                      jumpTo(section.id);
                    }}
                  >
                    {section.label}
                  </a>
                </li>
              ))}
            </ol>
          </nav>
        )}
      </div>

      {showBackToTop && (
        <button type="button" className="back-to-top" onClick={backToTop}>
          <span aria-hidden="true">↑</span> Back to top
        </button>
      )}
    </>
  );
}
