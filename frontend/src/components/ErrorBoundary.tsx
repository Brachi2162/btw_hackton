import { Component, type ReactNode } from "react";

/**
 * One failing part of the screen must not blank the whole app: it shows what
 * broke and a retry, and everything around it keeps working. The message is
 * shown so a person can report it.
 */
export class ErrorBoundary extends Component<{ children: ReactNode; label: string }, { error: Error | null }> {
  state: { error: Error | null } = { error: null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error) {
    console.error(`[${this.props.label}]`, error);
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    return (
      <div className="error-box card" role="alert">
        <p>משהו השתבש ב{this.props.label}. שאר המערכת ממשיכה לעבוד.</p>
        <p className="muted small" dir="ltr">
          {error.message}
        </p>
        <button className="btn" onClick={() => this.setState({ error: null })}>
          נסי שוב
        </button>
      </div>
    );
  }
}
