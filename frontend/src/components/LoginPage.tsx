import { useState, type FormEvent } from "react";
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

export default function LoginPage() {
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

        {error && (
          <div>
            <p role="alert" style={{ color: "#b91c1c", margin: 0, fontSize: 14 }}>{error}</p>
            {/* Passwords are reset by an administrator (Users page), not
                self-service: there is no email service to verify identity,
                and an unverified reset would let anyone who knows an email
                address take over that account. */}
            <p style={{ margin: "4px 0 0", fontSize: 13, color: "#667085" }}>
              Forgot your password? Contact your administrator to reset it.
            </p>
          </div>
        )}

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
