import React from 'react';

export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, info) {
    console.error('[ErrorBoundary]', error, info);
  }

  render() {
    if (!this.state.hasError) return this.props.children;

    return (
      <div style={{
        display:         'flex',
        alignItems:      'center',
        justifyContent:  'center',
        minHeight:       '60vh',
        padding:         '24px'
      }}>
        <div style={{
          textAlign:     'center',
          maxWidth:      '420px',
          padding:       '40px 32px',
          background:    '#ffffff',
          border:        '1px solid #dde3ed',
          borderRadius:  '10px',
          boxShadow:     '0 4px 16px rgba(0,55,129,.06)'
        }}>
          <div style={{ fontSize: 36, marginBottom: 16 }}>⚠️</div>
          <h2 style={{ fontSize: 18, fontWeight: 700, color: '#111827', marginBottom: 8 }}>
            Something went wrong
          </h2>
          <p style={{ fontSize: 13.5, color: '#52637a', marginBottom: 20, lineHeight: 1.6 }}>
            An unexpected error occurred. Please try refreshing the page.
            If the problem persists, contact your administrator.
          </p>
          {this.state.error && (
            <p style={{ fontSize: 11, color: '#8e9bb0', marginBottom: 20, fontFamily: 'monospace' }}>
              {this.state.error.message}
            </p>
          )}
          <button
            onClick={() => window.location.reload()}
            style={{
              padding: '9px 24px', background: '#003781', color: '#fff',
              border: 'none', borderRadius: 6, fontSize: 13, fontWeight: 500,
              cursor: 'pointer'
            }}
          >
            Refresh page
          </button>
        </div>
      </div>
    );
  }
}
