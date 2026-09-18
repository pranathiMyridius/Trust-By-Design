import { useEffect, useState } from "react";
import "./App.css";

import {
  getAssessments,
  analyzeAssessment,
  getAllAuditEvents,
  type Assessment,
  type AuditEvent,
} from "./api/assessments";

import CreateAssessment from "./components/CreateAssessment";
import AssessmentDetails from "./components/AssessmentDetails";
import AssessmentsPage from "./components/AssessmentsPage";
import AuditHistoryPage from "./components/AuditHistoryPage";
import AssessmentWorkflow from "./components/AssessmentWorkflow";

function App() {
  const [assessments, setAssessments] = useState<Assessment[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [showCreateForm, setShowCreateForm] = useState(false);
const [currentPage, setCurrentPage] = useState<
      "dashboard" | "assessments" | "audit"
    >("dashboard");

  const [analyzingId, setAnalyzingId] = useState<number | null>(null);

  const [selectedAssessment, setSelectedAssessment] =
    useState<Assessment | null>(null);
  const [auditEvents, setAuditEvents] = useState<AuditEvent[]>([]);

  /*
   * Load assessments when the application starts
   */
  useEffect(() => {
    loadAssessments();
    loadAuditEvents();
  }, []);
   /*
   * Opening an assessment (from dashboard, Assessments page, or Audit
   * History) marks it as recently opened so the dashboard's "Recent
   * Assessments" list reflects it.
   */
  function openAssessment(assessment: Assessment) {
    recordAssessmentOpened(assessment.id);
    setSelectedAssessment(assessment);
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
      setError("Unable to load assessments.");
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
  async function handleAnalyze(assessmentId: number) {
    try {
      setAnalyzingId(assessmentId);
      setError("");

      await analyzeAssessment(assessmentId);

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
      setError("Unable to analyze assessment.");
    } finally {
      setAnalyzingId(null);
    }
  }

  /*
   * Dashboard statistics
   */
  const totalAssessments = assessments.length;

  const highRisk = assessments.filter(
    (assessment) => assessment.risk_level === "HIGH"
  ).length;

  const mediumRisk = assessments.filter(
    (assessment) => assessment.risk_level === "MEDIUM"
  ).length;

  const lowRisk = assessments.filter(
    (assessment) => assessment.risk_level === "LOW"
  ).length;
const recentlyOpenedMap = readRecentlyOpenedMap();

const recentAssessments = [...assessments]
  .sort((a, b) => {
    const aOpened = recentlyOpenedMap[a.id] ?? 0;
    const bOpened = recentlyOpenedMap[b.id] ?? 0;

    if (aOpened !== bOpened) {
      return bOpened - aOpened;
    }

    return (
      new Date(b.created_at).getTime() -
      new Date(a.created_at).getTime()
    );
  })
  .slice(0, 5);


  /*
   * Render application
   */
  return (
    <div className="app">
      {/* =========================
          TOP BAR
      ========================== */}

      <header className="topbar">
        <div className="brand">
          <div className="brand-mark">R</div>

          <div>
            <h1>Risk Assessment Workbench</h1>
            <span>Enterprise Risk Management</span>
          </div>
        </div>

        <button
          className="new-assessment-button"
          onClick={() => {
            setSelectedAssessment(null);
            setShowCreateForm(true);
          }}
        >
          + New Assessment
        </button>
      </header>

      {/* =========================
          APPLICATION BODY
      ========================== */}

      <div className="app-body">
        {/* =========================
            SIDEBAR
        ========================== */}

        <aside className="sidebar">
          <nav>
            <button
              className={`nav-item ${
                currentPage === "dashboard" ? "active" : ""
              }`}
              onClick={() => {
                setCurrentPage("dashboard");
                setShowCreateForm(false);
                setSelectedAssessment(null);
              }}
            >
              <span>▦</span>
              Dashboard
            </button>

           <button
            className={`nav-item ${
              currentPage === "assessments" ? "active" : ""
            }`}
            onClick={() => {
              setCurrentPage("assessments");
              setShowCreateForm(false);
              setSelectedAssessment(null);
            }}
          >
            <span>☷</span>
            Assessments
          </button>
          <button
            className={`nav-item ${
              currentPage === "audit" ? "active" : ""
            }`}
            onClick={() => {
              setCurrentPage("audit");
              setShowCreateForm(false);
              setSelectedAssessment(null);
            }}
          >
            <span>◷</span>
            Audit History
          </button>
          </nav>
        </aside>

        {/* =========================
            MAIN CONTENT
        ========================== */}

        <main className="main-content">
         {showCreateForm ? (
          <CreateAssessment
            onCreated={() => {
              setShowCreateForm(false);
              loadAssessments();
            }}
            onCancel={() => {
              setShowCreateForm(false);
            }}
          />
        ) : selectedAssessment ? (
  <AssessmentWorkflow
    assessment={selectedAssessment}
    onBack={() => {
      setSelectedAssessment(null);
    }}
  />
        ) : currentPage === "assessments" ? (
          <AssessmentsPage
            assessments={assessments}
            onSelectAssessment={(assessment) => {
              openAssessment(assessment);
          }}

            onCreateAssessment={() => {
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
          ) : (
            /* =========================
               DASHBOARD
            ========================== */

            <>
              {/* PAGE HEADER */}

              <div className="page-header">
                <div>
                  <h2>Dashboard</h2>
                  <p>Monitor and manage business change risk assessments.</p>
                </div>
              </div>

              {/* =========================
                  STATISTICS
              ========================== */}

              <section className="stats-grid">
                <div className="stat-card">
                  <span>Total Assessments</span>
                  <strong>{loading ? "..." : totalAssessments}</strong>
                </div>

                <div className="stat-card">
                  <span>High Risk</span>
                  <strong>{loading ? "..." : highRisk}</strong>
                </div>

                <div className="stat-card">
                  <span>Medium Risk</span>
                  <strong>{loading ? "..." : mediumRisk}</strong>
                </div>

                <div className="stat-card">
                  <span>Low Risk</span>
                  <strong>{loading ? "..." : lowRisk}</strong>
                </div>
                
              </section>

              {/* =========================
                  RECENT ASSESSMENTS
              ========================== */}

              <section className="content-card">
                <div className="card-header">
                  <div>
                    <h3>Recent Assessments</h3>
                    <p>Latest business changes submitted for assessment.</p>
                  </div>
                </div>

                {/* =========================
                    ERROR STATE
                ========================== */}

                {error && (
                  <div className="empty-state">
                    <h3>{error}</h3>

                    <button className="primary-button" onClick={loadAssessments}>
                      Try Again
                    </button>
                  </div>
                )}

                {/* =========================
                    EMPTY STATE
                ========================== */}

                {!loading && !error && assessments.length === 0 && (
                  <div className="empty-state">
                    <div className="empty-icon">✓</div>

                    <h3>No assessments yet</h3>

                    <p>
                      Create an assessment to analyze customer, operational,
                      financial, compliance, technology, and geographic risks.
                    </p>

                    <button
                      className="primary-button"
                      onClick={() => setShowCreateForm(true)}
                    >
                      Create Assessment
                    </button>
                  </div>
                )}

                {/* =========================
                    ASSESSMENT LIST
                ========================== */}

                {!loading && !error && assessments.length > 0 && (
                  <div className="assessment-list">
                    {recentAssessments.map((assessment) => (
                      <div
                        className="assessment-row"
                        key={assessment.id}
                        onClick={() => openAssessment(assessment)}
                      >
                        {/* ASSESSMENT INFO */}

                        <div>
                          <h4>{assessment.title}</h4>
                          <span className="change-type">
                            {assessment.change_type}
                          </span>
                        </div>

                        {/* ASSESSMENT STATUS */}

                        <div className="assessment-status">
                          <span>{assessment.status}</span>

                          {/* RISK RESULT */}

                          {assessment.overall_score != null ? (
                            <>
                              <strong>{assessment.overall_score}</strong>
                              <span>
                                {assessment.risk_level ?? "NOT ANALYZED"}
                              </span>
                            </>
                          ) : (
                            /* ANALYZE BUTTON */

                            <button
                              className="analyze-button"
                              onClick={(event) => {
                                event.stopPropagation();
                                handleAnalyze(assessment.id);
                              }}
                              disabled={analyzingId === assessment.id}
                            >
                              {analyzingId === assessment.id
                                ? "Analyzing..."
                                : "Analyze"}
                            </button>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </section>
            </>
          )}
        </main>
      </div>
    </div>
  );
}
/* =========================================
   RECENTLY-OPENED TRACKING
   "Recent Assessments" on the dashboard should reflect the
   assessments the user most recently opened, not the ones most
   recently created. We stamp an assessment's id with the current
   time whenever it's opened, persist that in localStorage, and
   sort by that stamp (falling back to created_at for records that
   have never been opened yet).
   ========================================= */

const RECENT_OPENED_KEY = "recently-opened-assessments";

function readRecentlyOpenedMap(): Record<number, number> {
  if (typeof window === "undefined") {
    return {};
  }

  try {
    const raw = window.localStorage.getItem(RECENT_OPENED_KEY);
    if (!raw) {
      return {};
    }
    const parsed = JSON.parse(raw);
    return typeof parsed === "object" && parsed !== null ? parsed : {};
  } catch (error) {
    console.error(error);
    return {};
  }
}

function recordAssessmentOpened(assessmentId: number) {
  if (typeof window === "undefined") {
    return;
  }

  try {
    const current = readRecentlyOpenedMap();
    current[assessmentId] = Date.now();
    window.localStorage.setItem(RECENT_OPENED_KEY, JSON.stringify(current));
  } catch (error) {
    console.error(error);
  }
}

export default App;