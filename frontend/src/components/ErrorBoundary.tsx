import { Component } from 'react';
import type { ErrorInfo, ReactNode } from 'react';

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
  info: ErrorInfo | null;
}

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null, info: null };

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    this.setState({ error, info });
    // eslint-disable-next-line no-console
    console.error('[ErrorBoundary] caught:', error, info);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="p-8 max-w-4xl">
        <h2 className="text-xl font-semibold text-red mb-3">Component crashed</h2>
        <p className="text-text-2 mb-4">
          A runtime error stopped rendering. Details below — also check your browser console (F12).
        </p>
        <div className="bg-surface-2 border border-red/40 rounded-lg p-4 mb-3">
          <p className="text-sm font-mono text-red whitespace-pre-wrap break-all">
            {this.state.error.name}: {this.state.error.message}
          </p>
        </div>
        {this.state.error.stack && (
          <details className="bg-surface-2 border border-border rounded-lg p-4 mb-3">
            <summary className="text-text-2 text-sm cursor-pointer">Stack trace</summary>
            <pre className="text-xs text-text-3 mt-2 overflow-auto whitespace-pre-wrap">
              {this.state.error.stack}
            </pre>
          </details>
        )}
        {this.state.info?.componentStack && (
          <details className="bg-surface-2 border border-border rounded-lg p-4">
            <summary className="text-text-2 text-sm cursor-pointer">Component stack</summary>
            <pre className="text-xs text-text-3 mt-2 overflow-auto whitespace-pre-wrap">
              {this.state.info.componentStack}
            </pre>
          </details>
        )}
        <button
          onClick={() => this.setState({ error: null, info: null })}
          className="mt-4 px-3 py-1.5 rounded-lg bg-accent hover:bg-accent-2 text-white text-sm transition-colors"
        >
          Try again
        </button>
      </div>
    );
  }
}
