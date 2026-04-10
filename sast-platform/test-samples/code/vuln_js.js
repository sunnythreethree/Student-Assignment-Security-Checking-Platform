/**
 * vuln_js.js — intentionally vulnerable JavaScript for scanner testing.
 * Expected: findings from Semgrep p/javascript (eval, XSS, hardcoded secret).
 */

// dangerous-eval: arbitrary code execution
function runUserCode(input) {
    return eval(input);
}

// XSS via document.write with user-controlled input
function greetUser(username) {
    document.write("<h1>Welcome, " + username + "</h1>");
}

// Hardcoded API token
const API_TOKEN = "sk-hardcoded-secret-token-abc123";

// SQL injection via string concatenation
function loginQuery(username, password) {
    var query = "SELECT * FROM users WHERE name='" + username +
                "' AND password='" + password + "'";
    db.execute(query);
}

// XSS via innerHTML with user input
function renderMessage(msg) {
    document.getElementById("output").innerHTML = msg;
}
