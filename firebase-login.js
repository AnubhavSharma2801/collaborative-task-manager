'use strict';

//import firebase
import { initializeApp } from "https://www.gstatic.com/firebasejs/11.4.0/firebase-app.js";
import {getAuth, createUserWithEmailAndPassword, signInWithEmailAndPassword, signOut } from "https://www.gstatic.com/firebasejs/11.4.0/firebase-auth.js"

const firebaseConfig = {
  apiKey: "AIzaSyBRK9ukDcpFFFzm0OMp0u46-jiL4yb1rcU",
  authDomain: "assignment2-455318.firebaseapp.com",
  projectId: "assignment2-455318",
  storageBucket: "assignment2-455318.firebasestorage.app",
  messagingSenderId: "197763944944",
  appId: "1:197763944944:web:17cb2b2300576d28618d35"
};
  
const app = initializeApp(firebaseConfig);
const auth = getAuth(app);

window.addEventListener("load", function(){
  updateUI(document.cookie);
  console.log("Hello world load");

  //signup of a new user to firebase
  document.getElementById("sign-up").addEventListener('click', function(){
      const email = document.getElementById("email").value 
      const password = document.getElementById("password").value

      // Basic validation
      if (!email || !password) {
          alert("Please enter both email and password");
          return;
      }

      if (password.length < 6) {
          alert("Password must be at least 6 characters long");
          return;
      }

      // Show loading state
      const signUpButton = document.getElementById("sign-up");
      signUpButton.disabled = true;
      signUpButton.textContent = "Creating account...";

      createUserWithEmailAndPassword(auth, email, password)
      .then((userCredential) => {
          //we have a created user
          const user = userCredential.user;
          console.log("User created successfully:", user.email);

          // First get the token
          return user.getIdToken().then(token => {
              // Set the token in cookie first
              document.cookie = "token=" + token + ";path=/;SameSite=Strict";
              
              // Then create user document in backend
              return fetch('/users', {
                  method: 'POST',
                  headers: {
                      'Content-Type': 'application/json'
                  },
                  body: JSON.stringify({ email: user.email })
              });
          });
      })
      .then(() => {
          window.location = "/";
      })
      .catch((error) => {
          console.error("Signup error:", error);
          let errorMessage = "An error occurred during signup.";
          
          switch(error.code) {
              case 'auth/email-already-in-use':
                  errorMessage = "This email is already registered. Please use a different email or login.";
                  break;
              case 'auth/invalid-email':
                  errorMessage = "Please enter a valid email address.";
                  break;
              case 'auth/weak-password':
                  errorMessage = "Password should be at least 6 characters long.";
                  break;
              default:
                  errorMessage = error.message;
          }
          
          alert(errorMessage);
      })
      .finally(() => {
          // Reset button state
          signUpButton.disabled = false;
          signUpButton.textContent = "Sign Up";
      });
  });
});

//login of a user to firebase
document.getElementById('login').addEventListener('click', function(){
  const email = document.getElementById('email').value
  const password = document.getElementById('password').value

  signInWithEmailAndPassword(auth, email, password)
  .then((userCredential) => {
      //we have signed in user
      const user = userCredential.user;
      console.log("logged in");

      //get the if token for the user who just logged in and force a redirect to/
      user.getIdToken().then((token) => {
          document.cookie = "token=" + token + ";path=/;SameSite=Strict";
          window.location = "/";
      });
  })
  .catch((error) => {
      //issue with signup that we will drop to console
      console.log(error.code + error.message);
      alert("Login failed: " + error.message);
  });
});

//signout from firebase
document.getElementById("sign-out").addEventListener('click', function(){
  signOut(auth)
  .then((output) => {
      //remove the ID token for the user and force a rediret to /
      document.cookie = "token=;path=/;SameSite=Strict";
      window.location = "/";
  });
});

// function that will update the UI for the user depending on if they are logged in or not by checking the passed in cookie
// that contains the token
function updateUI(cookie){
  var token = parseCookieToken(cookie);

  //if a user is logged in then disable the email, password, signup, and login UI elements and show the signout button and vice versa
  if(token.length > 0) {
      document.getElementById("login-box").hidden = true;
      document.getElementById("sign-out").hidden = false;
      document.getElementById("task-board-container").style.display = "block";
  } else {
      document.getElementById("login-box").hidden = false;
      document.getElementById("sign-out").hidden = true;
      document.getElementById("task-board-container").style.display = "none";
  }
}

//function that will take the and will return the value associated with it to the caller
function parseCookieToken(cookie){
  //split the cookie out on the basis of the semi colon
  var strings = cookie.split(';');

  //go through each of the strings
  for (let i=0; i< strings.length; i++){
      //split the strings based on the = sign. if the LHS is token then return the RHS immediately
      var temp = strings[i].split('=');
      if(temp[0].trim() == "token")
          return temp[1];
  }

  //if we go to this point then token wasn't in the cookie so return the empty string
  return "";
};