import { useState, type FormEvent } from "react";
import { requestPasswordReset, resetPassword } from "../api/auth";
import { useAuth } from "../context/AuthContext";
import { friendlyError } from "../utils/errorMessages";
import { FieldError, RequiredMarker } from "./FormFeedback";

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

// Stage 19: field-level validation for the sign-in form.
function validateLogin(email: string, password: string) {
  const errors: { email?: string; password?: string } = {};
  if (!email.trim()) {
    errors.email = "Enter your email address.";
  } else if (!EMAIL_PATTERN.test(email.trim())) {
    errors.email = "Enter a valid email address, like name@company.com.";
  }
  if (!password) {
    errors.password = "Enter your password.";
  }
  return errors;
}

const inputStyle = {
  padding: "10px 12px",
  borderRadius: 8,
  border: "1px solid #d0d5dd",
};

const cardStyle = {
  background: "#ffffff",
  border: "1px solid #e5e7eb",
  borderRadius: 12,
  padding: 32,
  width: 360,
  display: "flex",
  flexDirection: "column",
  gap: 16,
} as const;

const pageStyle = {
  minHeight: "100vh",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  background: "#f4f6f8",
} as const;

// The reset link carries its token in the URL fragment (#reset=...).
function readResetToken(): string | null {
  const match = window.location.hash.match(/^#reset=(.+)$/);
  return match ? decodeURIComponent(match[1]) : null;
}

export default function LoginPage() {
  const [resetToken, setResetToken] = useState<string | null>(readResetToken);
  const [view, setView] = useState<"signin" | "forgot">("signin");
  const [notice, setNotice] = useState<string | null>(null);

  if (resetToken) {
    return (
      <ResetPasswordForm
        token={resetToken}
        onDone={(message) => {
          window.history.replaceState(null, "", window.location.pathname);
          setResetToken(null);
          setNotice(message);
          setView("signin");
        }}
        onCancel={() => {
          window.history.replaceState(null, "", window.location.pathname);
          setResetToken(null);
        }}
      />
    );
  }
  if (view === "forgot") {
    return <ForgotPasswordForm onBack={() => setView("signin")} />;
  }
  return (
    <SignInForm
      notice={notice}
      onForgot={() => {
        setNotice(null);
        setView("forgot");
      }}
    />
  );
}

function ForgotPasswordForm({ onBack }: { onBack: () => void }) {
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sent, setSent] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!EMAIL_PATTERN.test(email.trim())) {
      setError("Enter a valid email address, like name@company.com.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      setSent(await requestPasswordReset(email.trim()));
    } catch (err) {
      setError(friendlyError(err, "The reset request couldn't be sent. Please try again."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={pageStyle}>
      <form onSubmit={submit} noValidate aria-labelledby="forgot-heading" style={cardStyle}>
        <div>
          <h2 id="forgot-heading" style={{ margin: 0 }}>Reset your password</h2>
          <p style={{ margin: "4px 0 0", color: "#667085" }}>
            Enter your email address and we&apos;ll send you a link to choose a new password.
          </p>
        </div>
        {sent ? (
          <p role="status" style={{ margin: 0, color: "#15803d", fontSize: 14 }}>{sent}</p>
        ) : (
          <>
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <label htmlFor="forgot-email">Email</label>
              <input
                id="forgot-email"
                type="email"
                autoComplete="username"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                style={inputStyle}
              />
            </div>
            {error && <p role="alert" style={{ color: "#b91c1c", margin: 0, fontSize: 14 }}>{error}</p>}
            <button type="submit" className="primary-button" disabled={busy}>
              {busy ? "Sending..." : "Send reset link"}
            </button>
          </>
        )}
        <button type="button" className="link-button" onClick={onBack}>Back to sign in</button>
      </form>
    </div>
  );
}

function ResetPasswordForm({
  token,
  onDone,
  onCancel,
}: {
  token: string;
  onDone: (message: string) => void;
  onCancel: () => void;
}) {
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (password.length < 8) {
      setError("Password must be at least 8 characters.");
      return;
    }
    if (password !== confirm) {
      setError("The two passwords don't match.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      onDone(await resetPassword(token, password));
    } catch (err) {
      setError(friendlyError(err, "The password couldn't be reset. Please try again."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={pageStyle}>
      <form onSubmit={submit} noValidate aria-labelledby="reset-heading" style={cardStyle}>
        <div>
          <h2 id="reset-heading" style={{ margin: 0 }}>Choose a new password</h2>
          <p style={{ margin: "4px 0 0", color: "#667085" }}>At least 8 characters.</p>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <label htmlFor="reset-password">New password</label>
          <input
            id="reset-password"
            type="password"
            autoComplete="new-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            style={inputStyle}
          />
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <label htmlFor="reset-confirm">Confirm new password</label>
          <input
            id="reset-confirm"
            type="password"
            autoComplete="new-password"
            value={confirm}
            onChange={(event) => setConfirm(event.target.value)}
            style={inputStyle}
          />
        </div>
        {error && <p role="alert" style={{ color: "#b91c1c", margin: 0, fontSize: 14 }}>{error}</p>}
        <button type="submit" className="primary-button" disabled={busy}>
          {busy ? "Saving..." : "Set new password"}
        </button>
        <button type="button" className="link-button" onClick={onCancel}>Back to sign in</button>
      </form>
    </div>
  );
}

function SignInForm({ notice, onForgot }: { notice: string | null; onForgot: () => void }) {
  const { login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [touched, setTouched] = useState<{ email?: boolean; password?: boolean }>({});
  const [submitAttempted, setSubmitAttempted] = useState(false);

  const fieldErrors = validateLogin(email, password);
  const showEmailError = Boolean(fieldErrors.email && (touched.email || submitAttempted));
  const showPasswordError = Boolean(fieldErrors.password && (touched.password || submitAttempted));

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setSubmitAttempted(true);

    const errors = validateLogin(email, password);
    if (errors.email || errors.password) {
      document.getElementById(errors.email ? "login-email" : "login-password")?.focus();
      return;
    }

    setSubmitting(true);
    setError(null);

    try {
      await login(email, password);
    } catch (err) {
      setError(friendlyError(err, "Sign-in failed. Check your email and password and try again."));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "#f4f6f8",
      }}
    >
      <form
        onSubmit={handleSubmit}
        noValidate
        aria-labelledby="login-heading"
        style={{
          background: "#ffffff",
          border: "1px solid #e5e7eb",
          borderRadius: 12,
          padding: 32,
          width: 360,
          display: "flex",
          flexDirection: "column",
          gap: 16,
        }}
      >
        <div>
          <div
            style={{
              width: 38,
              height: 38,
              borderRadius: 10,
              background: "#172033",
              color: "white",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              fontWeight: 700,
              fontSize: 18,
              marginBottom: 12,
            }}
          >
            R
          </div>
          <h2 id="login-heading" style={{ margin: 0 }}>Risk Assessment Workbench</h2>
          <p style={{ margin: "4px 0 0", color: "#667085" }}>
            Sign in to continue.
          </p>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <label htmlFor="login-email">
            Email
            <RequiredMarker />
          </label>
          <input
            id="login-email"
            type="email"
            autoComplete="username"
            required
            aria-required="true"
            aria-invalid={showEmailError || undefined}
            aria-describedby={showEmailError ? "login-email-error" : undefined}
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            onBlur={() => setTouched((current) => ({ ...current, email: true }))}
            style={inputStyle}
          />
          {showEmailError && <FieldError id="login-email-error" message={fieldErrors.email} />}
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <label htmlFor="login-password">
            Password
            <RequiredMarker />
          </label>
          <input
            id="login-password"
            type="password"
            autoComplete="current-password"
            required
            aria-required="true"
            aria-invalid={showPasswordError || undefined}
            aria-describedby={showPasswordError ? "login-password-error" : undefined}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            onBlur={() => setTouched((current) => ({ ...current, password: true }))}
            style={inputStyle}
          />
          {showPasswordError && <FieldError id="login-password-error" message={fieldErrors.password} />}
        </div>

        {notice && <p role="status" style={{ margin: 0, color: "#15803d", fontSize: 14 }}>{notice}</p>}
        {error && <p role="alert" style={{ color: "#b91c1c", margin: 0, fontSize: 14 }}>{error}</p>}

        {/* Self-service reset: the link goes to the account's own email
            address, which is what verifies the person asking. */}
        <button type="button" className="link-button" onClick={onForgot} style={{ alignSelf: "flex-start" }}>
          Forgot your password?
        </button>

        <button
          type="submit"
          className="primary-button"
          disabled={submitting}
          style={{ marginTop: 8 }}
        >
          {submitting ? "Signing in..." : "Sign in"}
        </button>
      </form>
    </div>
  );
}
