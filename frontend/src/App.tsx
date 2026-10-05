import { useEffect, useRef, useState } from "react";
import "./App.css";

import {
  getAssessments,
  getAssessment,
  getAllAuditEvents,
  type Assessment,
  type AuditEvent,
} from "./api/assessments";

import CreateAssessment from "./components/CreateAssessment";
import { READ_ONLY_ROLES } from "./api/auth";
import SourceLibraryPage from "./components/SourceLibraryPage";
import AssessmentsPage from "./components/AssessmentsPage";
import AuditHistoryPage from "./components/AuditHistoryPage";
import AssessmentWorkflow from "./components/AssessmentWorkflow";
import RiskCalculatorPage from "./components/RiskCalculatorPage";
import ApprovalsPage from "./components/ApprovalsPage";
import SodExceptionsPage from "./components/SodExceptionsPage";
import UsersAdminPage from "./components/UsersAdminPage";
import DelegationsPage from "./components/DelegationsPage";
import SystemHealthPage from "./components/SystemHealthPage";
import GovernancePage from "./components/GovernancePage";
import RetentionAdminPage from "./components/RetentionAdminPage";
import { getRetentionPermissions } from "./api/retention";
import { analyzeAssessmentAsync, waitForProcessingJob } from "./api/processing";
import LoginPage from "./components/LoginPage";
import WorkQueuePage from "./components/WorkQueuePage";
import ReportsPage from "./components/ReportsPage";
import { REPORT_ROLES } from "./api/reports";
import { AuthProvider, useAuth } from "./context/AuthContext";
import DashboardPage, { type ActionFilter } from "./components/DashboardPage";
import { launchingWithinWeek } from "./utils/assessmentLifecycle";
import NavIcon, { type NavIconName } from "./components/NavIcons";
import ChatAssistant from "./components/ChatAssistant";
import NotificationBell from "./components/NotificationBell";
import { friendlyError } from "./utils/errorMessages";

type Page =
  | "dashboard"
  | "workqueue"
  | "assessments"
  | "reports"
  | "audit"
  | "calculator"
  | "approvals"
  | "delegations"
  | "users"
  | "system"
  | "governance"
  | "sources"
  | "sod"
  | "retention";

function App() {
  const { user, loading, logout } = useAuth();

  if (loading) {
    return null;
  }

  if (!user) {
    return <LoginPage />;
  }

  return <AuthenticatedApp user={user} onLogout={logout} />;
}

function AuthenticatedApp({
  user,
  onLogout,
}: {
  user: import("./api/auth").CurrentUser;
  onLogout: () => void;
}) {
  const [assessments, setAssessments] = useState<Assessment[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [showCreateForm, setShowCreateForm] = useState(false);
  // R1.3: a saved draft reopened in the intake form (null for a new request).
  const [editingDraft, setEditingDraft] = useState<Assessment | null>(null);
  const [currentPage, setCurrentPage] = useState<Page>("dashboard");
  const [search, setSearch] = useState("");
  const [actionFilter, setActionFilter] = useState<ActionFilter>("all");
  const [userMenuOpen, setUserMenuOpen] = useState(false);
  // The rarely used pages live behind a "More" entry in the sidebar.
  const [moreOpen, setMoreOpen] = useState(false);
  const moreRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!moreOpen) return;
    function onPointerDown(event: MouseEvent) {
      if (moreRef.current && !moreRef.current.contains(event.target as Node)) setMoreOpen(false);
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setMoreOpen(false);
    }
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [moreOpen]);

  const [analyzingId, setAnalyzingId] = useState<number | null>(null);
  // Stage 19: progress of the background risk-analysis job, e.g. "40%".
  const [analyzeProgress, setAnalyzeProgress] = useState("");

  const [selectedAssessment, setSelectedAssessment] =
    useState<Assessment | null>(null);
  const [auditEvents, setAuditEvents] = useState<AuditEvent[]>([]);
  // P5: the Retention page is shown to retention administrators, approvers
  // and report readers -- as the server says (it enforces it again).
  const [canViewRetention, setCanViewRetention] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getRetentionPermissions()
      .then((p) => !cancelled && setCanViewRetention(p.can_view))
      .catch(() => !cancelled && setCanViewRetention(false));
    return () => {
      cancelled = true;
    };
  }, [user.id]);

  /*
   * Load assessments when the application starts
   */
  useEffect(() => {
    loadAssessments();
    loadAuditEvents();
  }, []);

  /*
   * Closing an assessment -- by its back link or any sidebar page --
   * reloads the lists, so stage moves and ratings made while it was open
   * show on the dashboard straight away rather than after a page reload.
   */
  const selectedAssessmentId = selectedAssessment?.id ?? null;
  const hadAssessmentOpenRef = useRef(false);
  useEffect(() => {
    if (selectedAssessmentId === null && hadAssessmentOpenRef.current) {
      loadAssessments();
    }
    hadAssessmentOpenRef.current = selectedAssessmentId !== null;
  }, [selectedAssessmentId]);

  /*
   * Browser Back / Forward. The app keeps its place in React state, so
   * without this the browser's Back button leaves the app altogether
   * (e.g. Audit History -> assessment -> Back used to jump out of the
   * app). Each page / opened assessment / intake form is a history entry.
   */
  const navState = { page: currentPage, assessmentId: selectedAssessmentId, create: showCreateForm };
  const navRef = useRef(navState);
  navRef.current = navState;
  const assessmentsRef = useRef<Assessment[]>(assessments);
  assessmentsRef.current = assessments;
  const restoringRef = useRef(false);

  useEffect(() => {
    const prev = window.history.state as typeof navState | null;
    if (restoringRef.current) {
      if (prev && prev.page === navState.page && prev.assessmentId === navState.assessmentId && prev.create === navState.create) {
        restoringRef.current = false;
      }
      return;
    }
    if (!prev || typeof prev.page !== "string") {
      window.history.replaceState(navState, "");
    } else if (prev.page !== navState.page || prev.assessmentId !== navState.assessmentId || prev.create !== navState.create) {
      window.history.pushState(navState, "");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentPage, selectedAssessmentId, showCreateForm]);

  useEffect(() => {
    function onPopState(event: PopStateEvent) {
      const target = event.state as typeof navState | null;
      if (!target || typeof target.page !== "string") return;
      const current = navRef.current;
      restoringRef.current =
        target.page !== current.page || target.assessmentId !== current.assessmentId || target.create !== current.create;
      setCurrentPage(target.page);
      setShowCreateForm(Boolean(target.create));
      setUserMenuOpen(false);
      setMoreOpen(false);
      if (target.assessmentId == null) {
        setSelectedAssessment(null);
        return;
      }
      const known = assessmentsRef.current.find((item) => item.id === target.assessmentId);
      if (known) {
        setSelectedAssessment(known);
      } else {
        getAssessment(target.assessmentId)
          .then((fetched) => setSelectedAssessment(fetched))
          .catch(() => {
            restoringRef.current = false;
            setSelectedAssessment(null);
          });
      }
    }
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  function openAssessment(assessment: Assessment) {
    // Open immediately with whatever we already have (no loading flash),
    // then swap in a fresh copy — the in-memory `assessments` list is
    // only reloaded on a full page load, so it can be stale by the time
    // someone reopens an assessment (e.g. a Manual Scoring Calculator
    // draft or score saved earlier in the session wouldn't show up
    // otherwise, and would look like it had been "reset").
    setSelectedAssessment(assessment);

    getAssessment(assessment.id)
      .then((fresh) => {
        setSelectedAssessment((current) =>
          current?.id === fresh.id ? fresh : current
        );
        setAssessments((current) =>
          current.map((item) => (item.id === fresh.id ? fresh : item))
        );
      })
      .catch((err) => {
        // Non-fatal — the optimistic copy set above is still usable.
        console.error(err);
      });
  }

  // The assistant mentions assessments by id; open one from the list if we
  // have it, otherwise fetch it (the server decides whether it may be seen).
  function openAssessmentById(assessmentId: number) {
    const known = assessments.find((item) => item.id === assessmentId);
    if (known) {
      openAssessment(known);
      return;
    }
    getAssessment(assessmentId)
      .then((fetched) => openAssessment(fetched))
      .catch((err) => console.error(err));
  }

  /*
   * Get all assessments from backend
   */
  async function loadAssessments() {
    try {
      setLoading(true);
      setError("");

      const data = await getAssessments();

      setAssessments(data);

      return data;
    } catch (err) {
      console.error(err);
      setError(friendlyError(err, "We couldn't load your assessments. Please try again."));
      return [];
    } finally {
      setLoading(false);
    }
  }
  async function loadAuditEvents() {
  try {
    const events = await getAllAuditEvents();
    setAuditEvents(events);
  } catch (err) {
    console.error(err);
  }
}

  /*
   * Analyze an assessment
   */
  const canRunPipeline =
    user.role === "FCRM_ANALYST" || user.role === "MANAGER" || user.role === "ADMIN";

  async function handleAnalyze(assessmentId: number) {
    if (!canRunPipeline) {
      setError("Only an FCRM Analyst, Manager, or Admin can run risk analysis.");
      return;
    }

    try {
      setAnalyzingId(assessmentId);
      setError("");

      // Stage 19 (Performance): analysis runs as a background job; the page
      // stays usable and the button shows progress while we poll.
      const started = await analyzeAssessmentAsync(assessmentId);
      const job = await waitForProcessingJob(started.id, (update) =>
        setAnalyzeProgress(`${update.progress}%`)
      );

      if (job.status === "FAILED") {
        setError(job.error_message ?? "The risk analysis failed. Your assessment has been kept — please try again.");
      }

      const updatedAssessments = await loadAssessments();

      /*
       * If the currently selected assessment was analyzed,
       * refresh the selected assessment as well.
       */
      if (selectedAssessment?.id === assessmentId) {
        const updatedAssessment = updatedAssessments.find(
          (assessment) => assessment.id === assessmentId
        );

        if (updatedAssessment) {
          setSelectedAssessment(updatedAssessment);
        }
      }
    } catch (err) {
      console.error(err);
      setError(friendlyError(err, "The risk analysis couldn't be run. Please try again."));
    } finally {
      setAnalyzingId(null);
      setAnalyzeProgress("");
    }
  }

  const launchingSoonCount = assessments.filter(launchingWithinWeek).length;
  const provisionalCount = assessments.filter((a) => a.analysis_is_provisional).length;

  function goTo(page: Page) {
    setCurrentPage(page);
    setShowCreateForm(false);
    setSelectedAssessment(null);
    setUserMenuOpen(false);
    setMoreOpen(false);
  }

  const navItems: { page: Page; label: string; icon: NavIconName; visible: boolean }[] = [
    { page: "dashboard", label: "Dashboard", icon: "dashboard", visible: true },
    { page: "workqueue", label: "My Work Queue", icon: "queue", visible: true },
    { page: "assessments", label: "Assessments", icon: "files", visible: true },
    { page: "reports", label: "Reports", icon: "reports", visible: REPORT_ROLES.includes(user.role) },
    { page: "audit", label: "Audit History", icon: "history", visible: true },
    { page: "calculator", label: "Risk Calculator", icon: "calculator", visible: true },
    {
      page: "approvals",
      label: "Approvals",
      icon: "approvals",
      visible: user.role === "MANAGER" || user.role === "COMMITTEE_MEMBER",
    },
    {
      page: "delegations",
      label: "Delegations",
      icon: "delegations",
      visible: user.role === "MANAGER" || user.role === "COMMITTEE_MEMBER" || user.role === "ADMIN",
    },
    { page: "users", label: "Users", icon: "users", visible: user.role === "ADMIN" },
    { page: "governance", label: "Risk Governance", icon: "governance", visible: user.role === "ADMIN" },
    // R5.1: everyone can search the approved library; a Policy Admin or Admin maintains it.
    { page: "sources", label: "Source Library", icon: "search", visible: true },
    // P3: SoD exceptions -- anyone may request; approval rights come from
    // governance designations (checked by the server).
    { page: "sod", label: "SoD Exceptions", icon: "shield", visible: true },
    { page: "retention", label: "Retention & Legal Holds", icon: "calendar", visible: canViewRetention },
    { page: "system", label: "System Health", icon: "system", visible: user.role === "ADMIN" },
  ];

  // Everyday pages stay in the rail; the rest sit under "More".
  const PRIMARY_PAGES: Page[] = ["dashboard", "workqueue", "assessments", "reports", "approvals"];
  const visibleItems = navItems.filter((item) => item.visible);
  const primaryItems = visibleItems.filter((item) => PRIMARY_PAGES.includes(item.page));
  const moreItems = visibleItems.filter((item) => !PRIMARY_PAGES.includes(item.page));
  const moreActive = !showCreateForm && !selectedAssessment && moreItems.some((item) => item.page === currentPage);

  // R15.1: an Auditor or Read-only Executive sees but cannot change.
  const readOnly = READ_ONLY_ROLES.includes(user.role);
  const onMainPage = !showCreateForm && !selectedAssessment;
  const displayName = user.full_name ?? user.email;
  const initials =
    displayName
      .split(/[\s@._-]+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((part) => part.charAt(0).toUpperCase())
      .join("") || "U";

  /*
   * Render application
   */
  return (
    <div className="app">
      {/* Stage 19: keyboard users can bypass the header + sidebar. */}
      <a className="skip-link" href="#main-content" onClick={(event) => {
        event.preventDefault();
        const main = document.getElementById("main-content");
        main?.focus();
        main?.scrollIntoView();
      }}>
        Skip to main content
      </a>

      <div className="app-body">
        {/* =========================
            SIDEBAR (icon rail)
        ========================== */}

        <aside className="sidebar">
          <div className="rail-brand" title="Financial Crime Risk Assessment Workbench">
            <NavIcon name="shield" />
          </div>

          <nav aria-label="Main navigation" className="rail-nav">
            {primaryItems
              .flatMap((item) => {
                const button = (
                  <button
                    key={item.page}
                    className={`nav-item ${currentPage === item.page && onMainPage ? "active" : ""}`}
                    aria-current={currentPage === item.page && onMainPage ? "page" : undefined}
                    aria-label={item.label}
                    data-tooltip={item.label}
                    type="button"
                    onClick={() => goTo(item.page)}
                  >
                    <NavIcon name={item.icon} />
                  </button>
                );
                if (item.page !== "dashboard" || readOnly) return [button];
                return [
                  button,
                  <button
                    key="new-assessment"
                    className={`nav-item ${showCreateForm ? "active" : ""}`}
                    aria-current={showCreateForm ? "page" : undefined}
                    aria-label="New Assessment"
                    data-tooltip="New Assessment"
                    type="button"
                    onClick={() => {
                      setSelectedAssessment(null);
                      setEditingDraft(null);
                      setShowCreateForm(true);
                      setUserMenuOpen(false);
                    }}
                  >
                    <NavIcon name="file-plus" />
                  </button>,
                ];
              })}
            {moreItems.length > 0 && (
              <div className="rail-more" ref={moreRef}>
                <button
                  className={`nav-item ${moreActive ? "active" : ""}`}
                  aria-label="More"
                  aria-expanded={moreOpen}
                  aria-haspopup="true"
                  data-tooltip={moreOpen ? undefined : "More"}
                  type="button"
                  onClick={() => {
                    setMoreOpen((open) => !open);
                    setUserMenuOpen(false);
                  }}
                >
                  <NavIcon name="more" />
                </button>
                {moreOpen && (
                  <div className="rail-more-menu" aria-label="More pages">
                    {moreItems.map((item) => (
                      <button
                        key={item.page}
                        type="button"
                        className={currentPage === item.page && onMainPage ? "active" : ""}
                        aria-current={currentPage === item.page && onMainPage ? "page" : undefined}
                        onClick={() => goTo(item.page)}
                      >
                        <NavIcon name={item.icon} size={16} /> {item.label}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}
          </nav>

          <div className="rail-footer">
            <button
              type="button"
              className="rail-avatar"
              aria-label={`Account: ${displayName}`}
              aria-expanded={userMenuOpen}
              aria-haspopup="menu"
              onClick={() => setUserMenuOpen((open) => !open)}
            >
              {initials}
            </button>
            {userMenuOpen && (
              <div className="rail-menu" role="menu">
                <div className="rail-menu-who">
                  <strong>{displayName}</strong>
                  <span>{user.role.replace(/_/g, " ")}</span>
                </div>
                <button type="button" role="menuitem" onClick={onLogout}>
                  <NavIcon name="logout" size={16} /> Sign out
                </button>
              </div>
            )}
          </div>
        </aside>

        <div className="app-column">
          {/* =========================
              TOP BAR
          ========================== */}

          <header className="topbar">
            <div className="brand">
              {showCreateForm ? (
                <>
                  <h1>Create New Assessment</h1>
                  <span>Intake, Document Parsing &amp; Profile Auto-Extraction</span>
                </>
              ) : (
                <>
                  <h1>Financial Crime Risk Assessment Workbench</h1>
                  <span>FCRM Operations &amp; Client Risk Queue</span>
                </>
              )}
            </div>

            <div className="topbar-actions">
              <label className="topbar-search">
                <NavIcon name="search" size={16} />
                <span className="sr-only">Search assessments</span>
                <input
                  type="search"
                  placeholder="Search entity, ID, or risk profile…"
                  value={search}
                  onChange={(event) => {
                    setSearch(event.target.value);
                    if (event.target.value && !(currentPage === "dashboard" && onMainPage)) {
                      goTo("dashboard");
                    }
                  }}
                />
              </label>

              <NotificationBell refreshKey={assessments} userId={user.id} onOpen={openAssessmentById} />

              {provisionalCount > 0 && (
                <button
                  type="button"
                  className="topbar-pill topbar-pill-ai"
                  onClick={() => goTo("dashboard")}
                  title="AI results flagged provisional need a human reviewer"
                >
                  <NavIcon name="sparkle" size={14} />
                  {provisionalCount} provisional AI {provisionalCount === 1 ? "result" : "results"} to review
                </button>
              )}

              {launchingSoonCount > 0 && (
                <button
                  type="button"
                  className="topbar-pill topbar-pill-warn"
                  onClick={() => {
                    goTo("dashboard");
                    setActionFilter("launching");
                  }}
                >
                  <span aria-hidden="true">⚠</span>
                  {launchingSoonCount} launching within 7 days
                </button>
              )}
            </div>
          </header>

        {/* =========================
            MAIN CONTENT
        ========================== */}

        <main className="main-content" id="main-content" tabIndex={-1}>
         {readOnly && (
          <p className="nfr-banner" role="note" style={{ margin: "0 0 12px" }}>
            <span aria-hidden="true">🔒 </span>
            Read-only access ({user.role === "AUDITOR" ? "Auditor" : "Executive"}): you can view assessments,
            history and reports, but not change them.
          </p>
         )}
         {showCreateForm ? (
          <CreateAssessment
            key={editingDraft?.id ?? "new"}
            draft={editingDraft}
            onCreated={(created) => {
              setShowCreateForm(false);
              setEditingDraft(null);
              loadAssessments();
              // A submitted request goes straight on to its Intake step,
              // where the extracted business profile is confirmed.
              if (created) {
                openAssessment(created);
              }
            }}
            onCancel={() => {
              setShowCreateForm(false);
              setEditingDraft(null);
            }}
          />
        ) : selectedAssessment ? (
  <AssessmentWorkflow
    assessment={selectedAssessment}
    onBack={() => {
      setSelectedAssessment(null);
    }}
    user={user}
    onEditDraft={(draft) => {
      setSelectedAssessment(null);
      setEditingDraft(draft);
      setShowCreateForm(true);
    }}
  />
        ) : currentPage === "workqueue" ? (
          <WorkQueuePage
            onOpenAssessment={(assessmentId) => {
              const known = assessments.find((item) => item.id === assessmentId);
              if (known) {
                openAssessment(known);
              } else {
                getAssessment(assessmentId).then(openAssessment).catch(console.error);
              }
            }}
          />
        ) : currentPage === "reports" ? (
          <ReportsPage
            user={user}
            onOpenAssessment={(assessmentId) => {
              const known = assessments.find((item) => item.id === assessmentId);
              if (known) {
                openAssessment(known);
              } else {
                getAssessment(assessmentId).then(openAssessment).catch(console.error);
              }
            }}
          />
        ) : currentPage === "assessments" ? (
          <AssessmentsPage
            assessments={assessments}
            onSelectAssessment={(assessment) => {
              openAssessment(assessment);
          }}

            onCreateAssessment={() => {
              setEditingDraft(null);
              setShowCreateForm(true);
            }}
          />
        ) : currentPage === "audit" ? (
            <AuditHistoryPage
              auditEvents={auditEvents}
              assessments={assessments}
              onSelectAssessment={(assessment) => {
                openAssessment(assessment);
              }}
            />
          ) : currentPage === "calculator" ? (
            <RiskCalculatorPage />
          ) : currentPage === "approvals" ? (
            <ApprovalsPage
              user={user}
              assessments={assessments}
              onSelectAssessment={(assessment) => {
                openAssessment(assessment);
              }}
              onDecisionRecorded={() => {
                loadAssessments();
              }}
            />
          ) : currentPage === "delegations" ? (
            <DelegationsPage user={user} assessments={assessments} />
          ) : currentPage === "sod" ? (
            <SodExceptionsPage user={user} assessments={assessments} />
          ) : currentPage === "retention" ? (
            <RetentionAdminPage userRole={user.role} />
          ) : currentPage === "users" ? (
            <UsersAdminPage />
          ) : currentPage === "system" ? (
            <SystemHealthPage />
          ) : currentPage === "governance" ? (
            <GovernancePage />
          ) : currentPage === "sources" ? (
            <SourceLibraryPage canManage={user.role === "POLICY_ADMIN" || user.role === "ADMIN"} />
          ) : (
            <DashboardPage
              user={user}
              assessments={assessments}
              loading={loading}
              error={error}
              search={search}
              canRunPipeline={canRunPipeline}
              analyzingId={analyzingId}
              analyzeProgress={analyzeProgress}
              actionFilter={actionFilter}
              onActionFilterChange={setActionFilter}
              onRetry={loadAssessments}
              onOpenAssessment={openAssessment}
              onCreateAssessment={() => {
                setEditingDraft(null);
                setShowCreateForm(true);
              }}
              onAnalyze={handleAnalyze}
            />
          )}
        </main>
        </div>
      </div>

      {/* Read-only assistant: summaries, deadlines, risk explanations. */}
      <ChatAssistant
        assessmentId={selectedAssessmentId}
        onOpenAssessment={openAssessmentById}
      />
    </div>
  );
}

function AppWithAuth() {
  return (
    <AuthProvider>
      <App />
    </AuthProvider>
  );
}

export default AppWithAuth;