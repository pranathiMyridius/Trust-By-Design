import { useEffect, useRef, useState } from "react";

import { getWorkQueue, type WorkQueueItem } from "../api/workflow";
import NavIcon from "./NavIcons";

/*
 * Bell beside the search box: the tasks waiting on the signed-in user -- the
 * same list as their work queue (assigned to them, routed to their role, or
 * escalated to them). An item is "new" until the user opens the bell, and
 * turns new again if the assessment moves to another stage. Which items were
 * seen is remembered per user in this browser only -- losing it just makes
 * items look new again.
 */

function storageKey(userId: number) {
  return `rw-seen-assignments-${userId}`;
}

function loadSeen(userId: number): Set<string> {
  try {
    const raw = window.localStorage.getItem(storageKey(userId));
    return new Set(raw ? (JSON.parse(raw) as string[]) : []);
  } catch {
    return new Set();
  }
}

function saveSeen(userId: number, seen: Set<string>) {
  try {
    window.localStorage.setItem(storageKey(userId), JSON.stringify([...seen]));
  } catch {
    // Storage unavailable: items simply show as new again next time.
  }
}

const tokenOf = (item: WorkQueueItem) => `${item.assessment_id}:${item.workflow_status}:${item.reason}`;

const REFRESH_MS = 120_000;

interface Props {
  /** Changes whenever the assessment list reloads, to refresh the bell with it. */
  refreshKey: unknown;
  userId: number;
  onOpen: (assessmentId: number) => void;
}

export default function NotificationBell({ refreshKey, userId, onOpen }: Props) {
  const [open, setOpen] = useState(false);
  const [seen, setSeen] = useState<Set<string>>(() => loadSeen(userId));
  const rootRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => setSeen(loadSeen(userId)), [userId]);

  const [assigned, setAssigned] = useState<WorkQueueItem[]>([]);

  // Everything on the user's work queue: tasks assigned to them, tasks routed
  // to their role/ownership, and escalations. The server decides which.
  useEffect(() => {
    let cancelled = false;
    const load = () =>
      getWorkQueue()
        .then((queue) => {
          if (cancelled) return;
          const seenIds = new Set(queue.tasks.map((t) => t.assessment_id));
          setAssigned([...queue.tasks, ...queue.escalations.filter((e) => !seenIds.has(e.assessment_id))]);
        })
        .catch(() => undefined); // a failed refresh keeps the last list
    void load();
    const timer = window.setInterval(load, REFRESH_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [userId, refreshKey]);

  const unread = assigned.filter((a) => !seen.has(tokenOf(a)));

  useEffect(() => {
    if (!open) return;
    function onDown(event: MouseEvent) {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  function toggle() {
    const next = !open;
    setOpen(next);
    if (!next && unread.length > 0) {
      markAllSeen();
    }
  }

  function markAllSeen() {
    const updated = new Set(seen);
    assigned.forEach((a) => updated.add(tokenOf(a)));
    setSeen(updated);
    saveSeen(userId, updated);
  }

  return (
    <div className="bell" ref={rootRef}>
      <button
        type="button"
        className="bell-button"
        aria-haspopup="true"
        aria-expanded={open}
        aria-label={
          unread.length > 0 ? `Notifications, ${unread.length} new` : "Notifications"
        }
        onClick={toggle}
      >
        <NavIcon name="bell" size={18} />
        {unread.length > 0 && (
          <span className="bell-badge" aria-hidden="true">
            {unread.length > 9 ? "9+" : unread.length}
          </span>
        )}
      </button>

      {open && (
        <div className="bell-panel" role="region" aria-label="Waiting on you">
          <div className="bell-panel-head">
            <strong>Waiting on you</strong>
            {assigned.length > 0 && <span>{assigned.length}</span>}
          </div>
          {assigned.length === 0 ? (
            <p className="bell-empty">Nothing is waiting on you right now.</p>
          ) : (
            <ul>
              {assigned.map((a) => (
                <li key={`${a.assessment_id}-${a.reason}`}>
                  <button
                    type="button"
                    className={seen.has(tokenOf(a)) ? "" : "bell-new"}
                    onClick={() => {
                      markAllSeen();
                      setOpen(false);
                      onOpen(a.assessment_id);
                    }}
                  >
                    <strong>{a.title}</strong>
                    <span>
                      {a.reason === "ESCALATION" ? "Escalated · " : ""}
                      {a.reference_id ? `${a.reference_id} · ` : ""}
                      {a.workflow_status_label}
                      {a.status_due_at ? ` · due ${new Date(a.status_due_at).toLocaleDateString()}` : ""}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
