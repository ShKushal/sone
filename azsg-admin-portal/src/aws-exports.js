const awsconfig = {
  Auth: {
    Cognito: {
      userPoolId:       process.env.REACT_APP_USER_POOL_ID,
      userPoolClientId: process.env.REACT_APP_CLIENT_ID,
      region:           process.env.REACT_APP_REGION,
      loginWith: {
        email: true
      }
    }
  }
};

export default awsconfig;
