import React            from 'react';
import ReactDOM         from 'react-dom/client';
import { MsalProvider } from '@azure/msal-react';
import App              from './App';
import { msalInstance, initAuth } from './authConfig';

const root = ReactDOM.createRoot(document.getElementById('root'));

// finish any sign-in coming back from Microsoft before the first render
initAuth().then(({ redirectError }) => {
  root.render(
    <React.StrictMode>
      <MsalProvider instance={msalInstance}>
        <App redirectError={redirectError} />
      </MsalProvider>
    </React.StrictMode>
  );
});
