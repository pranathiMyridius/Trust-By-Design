import { useEffect, useState } from "react";
import { listUsers, createUser, updateUser, type CurrentUser } from "../api/auth";
import { POLICY_PENDING_LABEL, getGovernancePolicy, setUserDesignations } from "../api/governanceRecords";
import { friendlyError } from "../utils/errorMessages";
import { ErrorSummary, FieldError, RequiredMarker, type SummaryItem } from "./FormFeedback";

const ROLES: CurrentUser["role"][] = [
  "BUSINESS_USER",
  "FCRM_ANALYST",
  "MANAGER",
  "COMMITTEE_MEMBER",
  "ADMIN",
  "AUDITOR",
  "EXECUTIVE",
  "CONTROL_OWNER",
  "POLICY_ADMIN",
];

// R15.4: the scope lists are edited as comma-separated text.
function scopeText(values: string[] | undefined): string {
  return (values ?? []).join(", ");
}

function parseScope(text: string): string[] {
  return text
    .split(",")
    .map((value) => value.trim())
    .filter(Boolean);
}

function scopeSummary(user: CurrentUser): string {
  const parts = [
    user.scope_legal_entities?.length ? `Entities: ${scopeText(user.scope_legal_entities)}` : "",
    user.scope_business_units?.length ? `Units: ${scopeText(user.scope_business_units)}` : "",
    user.scope_countries?.length ? `Countries: ${scopeText(user.scope_countries)}` : "",
  ].filter(Boolean);
  return parts.length ? parts.join(" · ") : "Unrestricted";
}

export default function UsersAdminPage() {
  const [users, setUsers] = useState<CurrentUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [showCreateForm, setShowCreateForm] = useState(false);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const data = await listUsers();
      setUsers(data);
    } catch (err) {
      setError(friendlyError(err, "We couldn't load the user list. Please try again."));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  // P3: which designations each base role may hold (server policy).
  const [designationRoles, setDesignationRoles] = useState<Record<string, string[]>>({});
  useEffect(() => {
    getGovernancePolicy()
      .then((p) => setDesignationRoles(p.policy.designation_base_roles))
      .catch(() => setDesignationRoles({}));
  }, []);

  const managers = users.filter((user) => user.role === "MANAGER");

  function managerLabel(managerId: number | null): string {
    if (managerId == null) return "—";
    const manager = users.find((user) => user.id === managerId);
    return manager ? manager.full_name ?? manager.email : `#${managerId}`;
  }

  return (
    <div>
      <div className="page-header">
        <div>
          <h2>Users</h2>
          <p>Create accounts and assign roles / reporting managers.</p>
        </div>
        <button
          type="button"
          className="primary-button"
          aria-expanded={showCreateForm}
          onClick={() => setShowCreateForm((v) => !v)}
        >
          {showCreateForm ? "Cancel" : "+ New User"}
        </button>
      </div>

      {error && (
        <div className="empty-state" role="alert">
          <h3>{error}</h3>
          <button className="primary-button" onClick={load}>
            Try Again
          </button>
        </div>
      )}

      {showCreateForm && (
        <section className="content-card">
          <CreateUserForm
            managers={managers}
            onCreated={() => {
              setShowCreateForm(false);
              load();
            }}
          />
        </section>
      )}

      <section className="content-card">
        {loading ? (
          <p role="status" aria-live="polite">Loading…</p>
        ) : users.length === 0 ? (
          <div className="empty-state">
            <h3>No users yet</h3>
          </div>
        ) : (
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ textAlign: "left", borderBottom: "1px solid #e5e7eb" }}>
                <th style={{ padding: "8px 4px" }}>Name</th>
                <th style={{ padding: "8px 4px" }}>Email</th>
                <th style={{ padding: "8px 4px" }}>Role</th>
                <th style={{ padding: "8px 4px" }}>Manager</th>
                <th style={{ padding: "8px 4px" }}>Access scope</th>
                <th style={{ padding: "8px 4px" }}>
                  Governance designations
                  <div style={{ fontSize: 11, fontWeight: 400, color: "#9a3412" }}>{POLICY_PENDING_LABEL}</div>
                </th>
                <th style={{ padding: "8px 4px" }}>Active</th>
                <th style={{ padding: "8px 4px" }}><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {users.map((user) => (
                <UserRow
                  key={user.id}
                  user={user}
                  managers={managers}
                  managerLabel={managerLabel(user.manager_id)}
                  onUpdated={load}
                  allowedDesignations={designationRoles}
                />
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}

// Stage 19: field-level validation for the create-user form.
const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

type UserField = "email" | "password" | "role";

function validateNewUser(values: { email: string; password: string; role: string }) {
  const errors: Partial<Record<UserField, string>> = {};
  if (!values.email.trim()) {
    errors.email = "Enter the user's email address.";
  } else if (!EMAIL_PATTERN.test(values.email.trim())) {
    errors.email = "Enter a valid email address, like name@company.com.";
  }
  if (!values.password) {
    errors.password = "Enter a temporary password.";
  } else if (values.password.length < 8) {
    errors.password = `Password must be at least 8 characters (currently ${values.password.length}).`;
  }
  if (!values.role) {
    errors.role = "Choose a role.";
  }
  return errors;
}

const USER_FIELD_IDS: Record<UserField, string> = {
  email: "new-user-email",
  password: "new-user-password",
  role: "new-user-role",
};

function CreateUserForm({
  managers,
  onCreated,
}: {
  managers: CurrentUser[];
  onCreated: () => void;
}) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState("");
  const [role, setRole] = useState<CurrentUser["role"]>("BUSINESS_USER");
  const [managerId, setManagerId] = useState<string>("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [touched, setTouched] = useState<Partial<Record<UserField, boolean>>>({});
  const [submitAttempted, setSubmitAttempted] = useState(false);

  const fieldErrors = validateNewUser({ email, password, role });
  const visibleError = (field: UserField) =>
    touched[field] || submitAttempted ? fieldErrors[field] : undefined;
  const markTouched = (field: UserField) =>
    setTouched((current) => ({ ...current, [field]: true }));
  const describedBy = (field: UserField, extra?: string) =>
    [visibleError(field) ? `${USER_FIELD_IDS[field]}-error` : "", extra ?? ""]
      .filter(Boolean)
      .join(" ") || undefined;

  async function handleSubmit() {
    setSubmitAttempted(true);
    const errors = validateNewUser({ email, password, role });
    const firstInvalid = (Object.keys(USER_FIELD_IDS) as UserField[]).find((field) => errors[field]);
    if (firstInvalid) {
      document.getElementById(USER_FIELD_IDS[firstInvalid])?.focus();
      return;
    }

    setSubmitting(true);
    setError(null);
    try {
      await createUser({
        email,
        password,
        full_name: fullName || undefined,
        role,
        manager_id: managerId ? Number(managerId) : null,
      });
      setEmail("");
      setPassword("");
      setFullName("");
      setRole("BUSINESS_USER");
      setManagerId("");
      setTouched({});
      setSubmitAttempted(false);
      onCreated();
    } catch (err) {
      setError(friendlyError(err, "The user couldn't be created. Please check the details and try again."));
    } finally {
      setSubmitting(false);
    }
  }

  const summaryItems: SummaryItem[] = submitAttempted
    ? (Object.keys(USER_FIELD_IDS) as UserField[]).flatMap((field) => {
        const message = fieldErrors[field];
        return message ? [{ fieldId: USER_FIELD_IDS[field], message }] : [];
      })
    : [];

  return (
    <form
      noValidate
      aria-label="Create user"
      onSubmit={(event) => {
        event.preventDefault();
        handleSubmit();
      }}
      style={{ display: "flex", flexDirection: "column", gap: 12, maxWidth: 480 }}
    >
      <ErrorSummary id="new-user-error-summary" items={summaryItems} />

      <div>
        <label htmlFor={USER_FIELD_IDS.email}>
          Email
          <RequiredMarker />
        </label>
        <input
          id={USER_FIELD_IDS.email}
          type="email"
          autoComplete="off"
          aria-required="true"
          aria-invalid={visibleError("email") ? true : undefined}
          aria-describedby={describedBy("email")}
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          onBlur={() => markTouched("email")}
          style={{ width: "100%", padding: 8, marginTop: 4 }}
        />
        <FieldError id={`${USER_FIELD_IDS.email}-error`} message={visibleError("email")} />
      </div>

      <div>
        <label htmlFor={USER_FIELD_IDS.password}>
          Temporary password
          <RequiredMarker />
        </label>
        <input
          id={USER_FIELD_IDS.password}
          type="text"
          autoComplete="new-password"
          aria-required="true"
          aria-invalid={visibleError("password") ? true : undefined}
          aria-describedby={describedBy("password", `${USER_FIELD_IDS.password}-hint`)}
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          onBlur={() => markTouched("password")}
          style={{ width: "100%", padding: 8, marginTop: 4 }}
        />
        <small id={`${USER_FIELD_IDS.password}-hint`} style={{ color: "#667085" }}>
          At least 8 characters.
        </small>
        <FieldError id={`${USER_FIELD_IDS.password}-error`} message={visibleError("password")} />
      </div>

      <div>
        <label htmlFor="new-user-full-name">Full name</label>
        <input
          id="new-user-full-name"
          type="text"
          value={fullName}
          onChange={(event) => setFullName(event.target.value)}
          style={{ width: "100%", padding: 8, marginTop: 4 }}
        />
      </div>

      <div>
        <label htmlFor={USER_FIELD_IDS.role}>
          Role
          <RequiredMarker />
        </label>
        <select
          id={USER_FIELD_IDS.role}
          aria-required="true"
          aria-invalid={visibleError("role") ? true : undefined}
          aria-describedby={describedBy("role")}
          value={role}
          onChange={(event) => setRole(event.target.value as CurrentUser["role"])}
          onBlur={() => markTouched("role")}
          style={{ width: "100%", padding: 8, marginTop: 4 }}
        >
          {ROLES.map((r) => (
            <option key={r} value={r}>
              {r}
            </option>
          ))}
        </select>
        <FieldError id={`${USER_FIELD_IDS.role}-error`} message={visibleError("role")} />
      </div>

      {role === "BUSINESS_USER" && (
        <div>
          <label htmlFor="new-user-manager">
            Manager (required to submit for approval later)
          </label>
          <select
            id="new-user-manager"
            value={managerId}
            onChange={(event) => setManagerId(event.target.value)}
            style={{ width: "100%", padding: 8, marginTop: 4 }}
          >
            <option value="">— none —</option>
            {managers.map((manager) => (
              <option key={manager.id} value={manager.id}>
                {manager.full_name ?? manager.email}
              </option>
            ))}
          </select>
        </div>
      )}

      {error && <p role="alert" style={{ color: "#b91c1c", margin: 0 }}>{error}</p>}

      <button type="submit" className="primary-button" disabled={submitting}>
        {submitting ? "Creating…" : "Create User"}
      </button>
    </form>
  );
}

function DesignationsEditor({
  user,
  allowed,
  onUpdated,
}: {
  user: CurrentUser;
  allowed: string[];
  onUpdated: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [chosen, setChosen] = useState<string[]>(user.governance_designations ?? []);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const current = user.governance_designations ?? [];

  async function save() {
    if (!reason.trim()) {
      setError("A reason is required.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await setUserDesignations(user.id, chosen, reason.trim());
      setOpen(false);
      setReason("");
      onUpdated();
    } catch (err) {
      setError(friendlyError(err, "The designations couldn't be saved."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ fontSize: 13 }}>
      {current.length ? current.map((d) => d.replace(/_/g, " ").toLowerCase()).join(", ") : "—"}
      {allowed.length > 0 && !open && (
        <div>
          <button type="button" className="row-link-button" onClick={() => { setChosen(current); setOpen(true); }}>
            Change designations
          </button>
        </div>
      )}
      {open && (
        <fieldset style={{ marginTop: 4, border: "1px solid #e5e7eb", borderRadius: 4, padding: 6 }}>
          <legend style={{ fontSize: 12 }}>Designations for {user.full_name ?? user.email}</legend>
          {allowed.map((d) => (
            <label key={d} style={{ display: "block" }}>
              <input
                type="checkbox"
                checked={chosen.includes(d)}
                onChange={(e) => setChosen((c) => (e.target.checked ? [...c, d] : c.filter((x) => x !== d)))}
              />{" "}
              {d.replace(/_/g, " ").toLowerCase()}
            </label>
          ))}
          <input
            aria-label={`Reason for changing ${user.email}'s designations (required)`}
            placeholder="Reason (required)"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            style={{ width: "100%", marginTop: 4 }}
          />
          <div style={{ display: "flex", gap: 6, marginTop: 4 }}>
            <button type="button" disabled={busy} onClick={save}>Save</button>
            <button type="button" onClick={() => setOpen(false)}>Cancel</button>
          </div>
          {error && <span role="alert" className="field-error">{error}</span>}
        </fieldset>
      )}
    </div>
  );
}

function UserRow({
  user,
  managers,
  managerLabel,
  onUpdated,
  allowedDesignations,
}: {
  user: CurrentUser;
  managers: CurrentUser[];
  managerLabel: string;
  onUpdated: () => void;
  allowedDesignations: Record<string, string[]>;
}) {
  const [editing, setEditing] = useState(false);
  const [role, setRole] = useState(user.role);
  const [managerId, setManagerId] = useState<string>(
    user.manager_id != null ? String(user.manager_id) : ""
  );
  const [entities, setEntities] = useState(scopeText(user.scope_legal_entities));
  const [units, setUnits] = useState(scopeText(user.scope_business_units));
  const [countries, setCountries] = useState(scopeText(user.scope_countries));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [resettingPassword, setResettingPassword] = useState(false);
  const [newPassword, setNewPassword] = useState("");
  const [resetMessage, setResetMessage] = useState<string | null>(null);
  const [resetInvalid, setResetInvalid] = useState(false);

  async function handleSave() {
    setSaving(true);
    setError(null);
    try {
      await updateUser(user.id, {
        role,
        manager_id: managerId ? Number(managerId) : null,
        scope_legal_entities: parseScope(entities),
        scope_business_units: parseScope(units),
        scope_countries: parseScope(countries),
      });
      setEditing(false);
      onUpdated();
    } catch (err) {
      setError(friendlyError(err, "The user couldn't be updated. Please try again."));
    } finally {
      setSaving(false);
    }
  }

  async function handleToggleActive() {
    setSaving(true);
    setError(null);
    try {
      await updateUser(user.id, { is_active: !user.is_active });
      onUpdated();
    } catch (err) {
      setError(friendlyError(err, "The user couldn't be updated. Please try again."));
    } finally {
      setSaving(false);
    }
  }

  async function handleResetPassword() {
    if (newPassword.length < 8) {
      setResetInvalid(true);
      setResetMessage(`Password must be at least 8 characters (currently ${newPassword.length}).`);
      return;
    }
    setResetInvalid(false);
    setSaving(true);
    setResetMessage(null);
    try {
      await updateUser(user.id, { password: newPassword });
      setResetMessage(`Password set. They can now log in with: ${newPassword}`);
      setNewPassword("");
    } catch (err) {
      setResetMessage(friendlyError(err, "The password couldn't be reset. Please try again."));
    } finally {
      setSaving(false);
    }
  }

  return (
    <tr style={{ borderBottom: "1px solid #f2f4f7" }}>
      <td style={{ padding: "8px 4px" }}>{user.full_name ?? "—"}</td>
      <td style={{ padding: "8px 4px" }}>{user.email}</td>
      <td style={{ padding: "8px 4px" }}>
        {editing ? (
          <select
            aria-label={`Role for ${user.email}`}
            value={role}
            onChange={(event) => setRole(event.target.value as CurrentUser["role"])}
          >
            {ROLES.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
        ) : (
          user.role
        )}
      </td>
      <td style={{ padding: "8px 4px" }}>
        {editing ? (
          <select
            aria-label={`Manager for ${user.email}`}
            value={managerId}
            onChange={(event) => setManagerId(event.target.value)}
          >
            <option value="">— none —</option>
            {managers.map((manager) => (
              <option key={manager.id} value={manager.id}>
                {manager.full_name ?? manager.email}
              </option>
            ))}
          </select>
        ) : (
          managerLabel
        )}
      </td>
      <td style={{ padding: "8px 4px" }}>
        {editing ? (
          <span style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <input
              aria-label={`Legal entities ${user.email} is limited to (comma-separated; blank for all)`}
              placeholder="Legal entities (blank = all)"
              value={entities}
              onChange={(event) => setEntities(event.target.value)}
            />
            <input
              aria-label={`Business units ${user.email} is limited to (comma-separated; blank for all)`}
              placeholder="Business units (blank = all)"
              value={units}
              onChange={(event) => setUnits(event.target.value)}
            />
            <input
              aria-label={`Countries ${user.email} is limited to (comma-separated; blank for all)`}
              placeholder="Countries (blank = all)"
              value={countries}
              onChange={(event) => setCountries(event.target.value)}
            />
          </span>
        ) : (
          scopeSummary(user)
        )}
      </td>
      <td style={{ padding: "8px 4px" }}>
        {/* P3: designations allowed for this user's base role (server policy). */}
        <DesignationsEditor
          user={user}
          allowed={Object.entries(allowedDesignations)
            .filter(([, roles]) => roles.includes(user.role))
            .map(([designation]) => designation)}
          onUpdated={onUpdated}
        />
      </td>
      <td style={{ padding: "8px 4px" }}>{user.is_active ? "Yes" : "No"}</td>
      <td style={{ padding: "8px 4px", display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
        {editing ? (
          <>
            <button disabled={saving} onClick={handleSave}>
              Save
            </button>
            <button disabled={saving} onClick={() => setEditing(false)}>
              Cancel
            </button>
          </>
        ) : (
          <>
            <button disabled={saving} onClick={() => setEditing(true)}>
              Edit
            </button>
            <button disabled={saving} onClick={handleToggleActive}>
              {user.is_active ? "Deactivate" : "Activate"}
            </button>
            <button
              disabled={saving}
              onClick={() => {
                setResettingPassword((v) => !v);
                setResetMessage(null);
              }}
            >
              Reset Password
            </button>
          </>
        )}

        {resettingPassword && (
          <span style={{ display: "flex", gap: 6, alignItems: "center" }}>
            <input
              type="text"
              aria-label={`New password for ${user.email} (at least 8 characters)`}
              aria-invalid={resetInvalid || undefined}
              aria-describedby={resetMessage ? `reset-msg-${user.id}` : undefined}
              placeholder="New password (min 8 chars)"
              value={newPassword}
              onChange={(event) => setNewPassword(event.target.value)}
              style={{ padding: 4 }}
            />
            <button disabled={saving} onClick={handleResetPassword}>
              Set
            </button>
          </span>
        )}

        {error && <span role="alert" style={{ color: "#b91c1c" }}>{error}</span>}
        {resetMessage && (
          <span
            id={`reset-msg-${user.id}`}
            role={resetInvalid ? "alert" : "status"}
            className={resetInvalid ? "field-error" : undefined}
            style={resetInvalid ? undefined : { color: "#0f766e" }}
          >
            {resetMessage}
          </span>
        )}
      </td>
    </tr>
  );
}
