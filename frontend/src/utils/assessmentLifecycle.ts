import type { Assessment } from "../api/assessments";

/*
 * Lifecycle buckets shared by the dashboard KPIs and the header alerts.
 */

export const DAY_MS = 24 * 60 * 60 * 1000;

// Lifecycle buckets for the KPI cards (workflow_status values from
// backend/app/services/workflow.py).
const PENDING_REVIEW = new Set([
  "ANALYST_REVIEW",
  "CHALLENGE_REVIEW",
  "READY_FOR_COMMITTEE",
  "COMMITTEE_REVIEW",
]);
const APPROVED = new Set(["APPROVED", "APPROVED_WITH_CONDITIONS"]);
const INACTIVE = new Set([
  "APPROVED",
  "APPROVED_WITH_CONDITIONS",
  "REJECTED",
  "MANAGER_REJECTED",
  "DEFERRED",
  "CLOSED",
  "WITHDRAWN",
]);

export type Bucket = "draft" | "in_progress" | "pending_review" | "approved" | "inactive";

export function lifecycle(assessment: Assessment): string {
  return assessment.workflow_status ?? assessment.status;
}

export function bucketOf(assessment: Assessment): Bucket {
  const status = lifecycle(assessment);
  if (assessment.is_draft || status === "DRAFT") return "draft";
  if (APPROVED.has(status) || APPROVED.has(assessment.status)) return "approved";
  if (INACTIVE.has(status) || INACTIVE.has(assessment.status)) return "inactive";
  if (PENDING_REVIEW.has(status)) return "pending_review";
  return "in_progress";
}

export function isActive(assessment: Assessment): boolean {
  const bucket = bucketOf(assessment);
  return bucket !== "approved" && bucket !== "inactive";
}

export function launchingWithinWeek(assessment: Assessment): boolean {
  if (!assessment.expected_launch_date || !isActive(assessment)) return false;
  const launch = new Date(assessment.expected_launch_date);
  if (Number.isNaN(launch.getTime())) return false;
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  return launch.getTime() >= today.getTime() && launch.getTime() <= today.getTime() + 7 * DAY_MS;
}

