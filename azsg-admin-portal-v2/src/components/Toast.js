import React, { createContext, useCallback, useContext, useRef, useState } from 'react';

/**
 * Short confirmations in the bottom-right corner: "CSV downloaded", "Link copied" ...
 *   const toast = useToast();
 *   toast('Report downloaded');               // success
 *   toast('Could not copy', 'error');         // error | warning | info
 * Read out by screen readers (aria-live), never the only place an error is shown.
 */
const ToastContext = createContext(() => {});

export function useToast() {
  return useContext(ToastContext);
}

const ICON = { success: '✓', error: '✗', warning: '⚠', info: 'ℹ' };

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);
  const nextId = useRef(1);

  const dismiss = useCallback(id => setToasts(list => list.filter(t => t.id !== id)), []);

  const show = useCallback((text, type = 'success') => {
    const id = nextId.current++;
    setToasts(list => [...list.slice(-2), { id, text, type }]);     // at most 3 on screen
    setTimeout(() => dismiss(id), type === 'error' || type === 'warning' ? 8000 : 4000);
  }, [dismiss]);

  return (
    <ToastContext.Provider value={show}>
      {children}
      <div className="toast-stack" role="status" aria-live="polite">
        {toasts.map(t => (
          <div key={t.id} className={`toast toast-${t.type}`}>
            <span className="toast-icon" aria-hidden="true">{ICON[t.type]}</span>
            <span className="toast-text">{t.text}</span>
            <button className="toast-close" onClick={() => dismiss(t.id)} aria-label="Dismiss">×</button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}
