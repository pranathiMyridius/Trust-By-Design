import { useEffect, useState } from "react";
import { listUsers, createUser, updateUser, sendResetEmail, type CurrentUser } from "../api/auth";
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

function roleLabel(role: string): string {
  const text = role.replace(/_/g, " ").toLowerCase();
  return text.charAt(0).toUpperCase() + text.slice(1);
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
  // A short confirmation after creating a user (shown on the list).
  const [notice, setNotice] = useState<string | null>(null);

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

  const [search, setSearch] = useState("");
  const [roleFilter, setRoleFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState<"" | "active" | "inactive">("");
  const needle = search.trim().toLowerCase();
  const visibleUsers = users.filter((user) => {
    if (roleFilter && user.role !== roleFilter) return false;
    if (statusFilter === "active" && !user.is_active) return false;
    if (statusFilter === "inactive" && user.is_active) return false;
    if (!needle) return true;
    return `${user.full_name ?? ""} ${user.email}`.toLowerCase().includes(needle);
  });
  const hasFilters = Boolean(search || roleFilter || statusFilter);

  function managerLabel(managerId: number | null): string {
    if (managerId == null) return "—";
    const manager = users.find((user) => user.id === managerId);
    return manager ? manager.full_name ?? manager.email : `#${managerId}`;
  }

  if (showCreateForm) {
    return (
      <div className="users-page">
        <div className="page-header users-header">
          <div>
            <button type="button" className="users-back" onClick={() => setShowCreateForm(false)}>
              <span aria-hidden="true">←</span> Back to users
            </button>
            <h2>New user</h2>
            <p>Create an account. The new user is emailed a link to choose their own password.</p>
          </div>
        </div>

        <section className="content-card users-card">
          <header className="users-card-header">
            <h3>Account details</h3>
            <p>Email, name, role and reporting manager.</p>
          </header>
          <div className="users-card-body">
            <CreateUserForm
              managers={managers}
              onCancel={() => setShowCreateForm(false)}
              onCreated={(result) => {
                setShowCreateForm(false);
                setNotice(
                  result.email_sent
                    ? `User created. A welcome email with a link to set a password was sent to ${result.email}.`
                    : `User created. No email was sent (email isn't set up on this server), so share the temporary password with ${result.email} yourself.`
                );
                load();
              }}
            />
          </div>
        </section>
      </div>
    );
  }

  return (
    <div className="users-page">
      <div className="page-header users-header">
        <div>
          <h2>Users</h2>
          <p>Create accounts and assign roles / reporting managers.</p>
        </div>
        <button
          type="button"
          className="primary-button"
          onClick={() => {
            setNotice(null);
            setShowCreateForm(true);
          }}
        >
          + New User
        </button>
      </div>

      {notice && (
        <p className="users-notice" role="status">
          <span aria-hidden="true">✓ </span>
          {notice}
        </p>
      )}

      {error && (
        <div className="users-error" role="alert">
          <h3>{error}</h3>
          <button className="primary-button" onClick={load}>
            Try Again
          </button>
        </div>
      )}

      <section className="content-card users-card">
        <header className="users-card-header users-card-header-row">
          <div>
            <h3>All users</h3>
            <p>
              {hasFilters ? `${visibleUsers.length} of ${users.length}` : users.length} user{users.length === 1 ? "" : "s"}
            </p>
          </div>
        </header>
        {users.length > 0 && (
          <div className="users-filters">
            <div className="users-filter users-filter-search">
              <label htmlFor="users-search">Search</label>
              <input
                id="users-search"
                type="search"
                placeholder="Name or email…"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
              />
            </div>
            <div className="users-filter">
              <label htmlFor="users-role">Role</label>
              <select id="users-role" value={roleFilter} onChange={(event) => setRoleFilter(event.target.value)}>
                <option value="">All roles</option>
                {ROLES.map((r) => (
                  <option key={r} value={r}>
                    {roleLabel(r)}
                  </option>
                ))}
              </select>
            </div>
            <div className="users-filter">
              <label htmlFor="users-status">Status</label>
              <select
                id="users-status"
                value={statusFilter}
                onChange={(event) => setStatusFilter(event.target.value as "" | "active" | "inactive")}
              >
                <option value="">All</option>
                <option value="active">Active</option>
                <option value="inactive">Inactive</option>
              </select>
            </div>
            {hasFilters && (
              <button
                type="button"
                className="secondary-button"
                onClick={() => {
                  setSearch("");
                  setRoleFilter("");
                  setStatusFilter("");
                }}
              >
                Clear filters
              </button>
            )}
          </div>
        )}
        {loading ? (
          <p role="status" aria-live="polite" className="users-card-body">Loading…</p>
        ) : users.length === 0 ? (
          <div className="users-empty">
            <h4>No users yet</h4>
            <p>Create the first account with “+ New User”.</p>
          </div>
        ) : visibleUsers.length === 0 ? (
          <div className="users-empty">
            <h4>No users match these filters</h4>
          </div>
        ) : (
          <div className="users-table-wrap">
          <table className="users-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Email</th>
                <th>Role</th>
                <th>Manager</th>
                <th>Access scope</th>
                <th>
                  Governance designations
                  <div className="users-policy-note">{POLICY_PENDING_LABEL}</div>
                </th>
                <th>Active</th>
                <th><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {visibleUsers.map((user) => (
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
          </div>
        )}
      </section>
    </div>
  );
}

// Stage 19: field-level validation for the create-user form.
const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

type UserField = "email" | "password" | "role";

function validateNewUser(values: { email: string; password: string; role: string }) {
  // The password is optional: blank means "email them a link to choose one".
  const errors: Partial<Record<UserField, string>> = {};
  if (!values.email.trim()) {
    errors.email = "Enter the user's email address.";
  } else if (!EMAIL_PATTERN.test(values.email.trim())) {
    errors.email = "Enter a valid email address, like name@company.com.";
  }
  if (values.password && values.password.length < 8) {
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
  onCancel,
}: {
  managers: CurrentUser[];
  onCreated: (created: CurrentUser & { email_sent?: boolean }) => void;
  onCancel: () => void;
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
      const created = await createUser({
        email,
        password: password || undefined,
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
      onCreated(created);
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
      className="users-form"
    >
      <ErrorSummary id="new-user-error-summary" items={summaryItems} />

      <div className="users-field">
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
        />
        <FieldError id={`${USER_FIELD_IDS.email}-error`} message={visibleError("email")} />
      </div>

      <div className="users-field">
        <label htmlFor={USER_FIELD_IDS.password}>
          Temporary password (optional)
        </label>
        <input
          id={USER_FIELD_IDS.password}
          type="text"
          autoComplete="new-password"
          aria-invalid={visibleError("password") ? true : undefined}
          aria-describedby={describedBy("password", `${USER_FIELD_IDS.password}-hint`)}
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          onBlur={() => markTouched("password")}
        />
        <small id={`${USER_FIELD_IDS.password}-hint`} className="users-hint">
          Leave blank (recommended): the user gets an email with a link to choose their own password. Set one only if email is unavailable (at least 8 characters).
        </small>
        <FieldError id={`${USER_FIELD_IDS.password}-error`} message={visibleError("password")} />
      </div>

      <div className="users-field">
        <label htmlFor="new-user-full-name">Full name</label>
        <input
          id="new-user-full-name"
          type="text"
          value={fullName}
          onChange={(event) => setFullName(event.target.value)}
        />
      </div>

      <div className="users-field">
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
        >
          {ROLES.map((r) => (
            <option key={r} value={r}>
              {roleLabel(r)}
            </option>
          ))}
        </select>
        <FieldError id={`${USER_FIELD_IDS.role}-error`} message={visibleError("role")} />
      </div>

      {role === "BUSINESS_USER" && (
        <div className="users-field">
          <label htmlFor="new-user-manager">
            Manager (required to submit for approval later)
          </label>
          <select
            id="new-user-manager"
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
        </div>
      )}

      {error && <p role="alert" className="users-form-error">{error}</p>}

      <div className="users-form-actions">
        <button type="button" className="secondary-button" onClick={onCancel} disabled={submitting}>
          Cancel
        </button>
        <button type="submit" className="primary-button" disabled={submitting}>
          {submitting ? "Creating…" : "Create user"}
        </button>
      </div>
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
    <div className="users-designations">
      {current.length ? (
        <div className="users-tags">
          {current.map((d) => (
            <span key={d} className="users-tag">{d.replace(/_/g, " ").toLowerCase()}</span>
          ))}
        </div>
      ) : (
        "—"
      )}
      {allowed.length > 0 && !open && (
        <div>
          <button type="button" className="row-link-button" onClick={() => { setChosen(current); setOpen(true); }}>
            Change designations
          </button>
        </div>
      )}
      {open && (
        <fieldset className="users-fieldset">
          <legend>Designations for {user.full_name ?? user.email}</legend>
          {allowed.map((d) => (
            <label key={d} className="users-check">
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
            className="users-input"
          />
          <div className="users-inline-actions">
            <button type="button" className="secondary-button" disabled={busy} onClick={save}>Save</button>
            <button type="button" className="link-button" onClick={() => setOpen(false)}>Cancel</button>
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

  // Emails the user a one-time link to choose a new password; the admin
  // never sees or sets it.
  async function handleSendResetEmail() {
    setSaving(true);
    setResetInvalid(false);
    setResetMessage(null);
    try {
      setResetMessage(await sendResetEmail(user.id));
    } catch (err) {
      setResetInvalid(true);
      setResetMessage(friendlyError(err, "The reset email couldn't be sent. Please try again."));
    } finally {
      setSaving(false);
    }
  }

  return (
    <tr className={user.is_active ? "" : "users-row-inactive"}>
      <td>
        <div className="users-person">
          <span className="users-avatar" aria-hidden="true">
            {(user.full_name ?? user.email).charAt(0).toUpperCase()}
          </span>
          <div>
            <strong>{user.full_name ?? "—"}</strong>
            <span>{user.email}</span>
          </div>
        </div>
      </td>
      <td>
        {editing ? (
          <select
            aria-label={`Role for ${user.email}`}
            value={role}
            onChange={(event) => setRole(event.target.value as CurrentUser["role"])}
            className="users-input"
          >
            {ROLES.map((r) => (
              <option key={r} value={r}>
                {roleLabel(r)}
              </option>
            ))}
          </select>
        ) : (
          <span className="users-role">{roleLabel(user.role)}</span>
        )}
      </td>
      <td>
        {editing ? (
          <select
            aria-label={`Manager for ${user.email}`}
            value={managerId}
            onChange={(event) => setManagerId(event.target.value)}
            className="users-input"
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
      <td>
        {editing ? (
          <span className="users-scope-edit">
            <input
              aria-label={`Legal entities ${user.email} is limited to (comma-separated; blank for all)`}
              placeholder="Legal entities (blank = all)"
              value={entities}
              onChange={(event) => setEntities(event.target.value)}
              className="users-input"
            />
            <input
              aria-label={`Business units ${user.email} is limited to (comma-separated; blank for all)`}
              placeholder="Business units (blank = all)"
              value={units}
              onChange={(event) => setUnits(event.target.value)}
              className="users-input"
            />
            <input
              aria-label={`Countries ${user.email} is limited to (comma-separated; blank for all)`}
              placeholder="Countries (blank = all)"
              value={countries}
              onChange={(event) => setCountries(event.target.value)}
              className="users-input"
            />
          </span>
        ) : (
          <span className="users-scope">{scopeSummary(user)}</span>
        )}
      </td>
      <td>
        {/* P3: designations allowed for this user's base role (server policy). */}
        <DesignationsEditor
          user={user}
          allowed={Object.entries(allowedDesignations)
            .filter(([, roles]) => roles.includes(user.role))
            .map(([designation]) => designation)}
          onUpdated={onUpdated}
        />
      </td>
      <td>
        <span className={`users-status ${user.is_active ? "users-status-active" : "users-status-inactive"}`}>
          {user.is_active ? "Active" : "Inactive"}
        </span>
      </td>
      <td>
        <div className="users-actions">
        {editing ? (
          <>
            <button className="primary-button users-btn" disabled={saving} onClick={handleSave}>
              Save
            </button>
            <button className="secondary-button users-btn" disabled={saving} onClick={() => setEditing(false)}>
              Cancel
            </button>
          </>
        ) : (
          <>
            <button className="secondary-button users-btn" disabled={saving} onClick={() => setEditing(true)}>
              Edit
            </button>
            <button className="secondary-button users-btn" disabled={saving} onClick={handleToggleActive}>
              {user.is_active ? "Deactivate" : "Activate"}
            </button>
            <button
              className="secondary-button users-btn"
              disabled={saving || !user.is_active}
              title={user.is_active ? "Email this user a link to choose a new password" : "Activate the account first"}
              onClick={handleSendResetEmail}
            >
              {saving ? "Sending…" : "Send reset email"}
            </button>
          </>
        )}

        {error && <span role="alert" className="users-msg-error">{error}</span>}
        {resetMessage && (
          <span
            id={`reset-msg-${user.id}`}
            role={resetInvalid ? "alert" : "status"}
            className={resetInvalid ? "field-error" : "users-msg-ok"}
          >
            {resetMessage}
          </span>
        )}
        </div>
      </td>
    </tr>
  );
}
