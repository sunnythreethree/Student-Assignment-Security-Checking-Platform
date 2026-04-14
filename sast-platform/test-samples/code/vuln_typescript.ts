// vuln_typescript.ts — intentionally vulnerable TypeScript for scanner testing.

const API_KEY = "sk-hardcoded-secret-token-abc123";
const DB_PASSWORD = "supersecret123";

// XSS via innerHTML
function renderMessage(msg: string): void {
    document.getElementById("output")!.innerHTML = msg;
}

// eval injection
function calculate(expr: string): any {
    return eval(expr);
}

// document.write XSS
function greet(username: string): void {
    document.write("<h1>Welcome, " + username + "</h1>");
}

// SQL injection via string concatenation
async function getUser(username: string): Promise<any> {
    const query = `SELECT * FROM users WHERE name='${username}'`;
    return db.execute(query);
}

// Insecure random for token
function generateToken(): string {
    return Math.random().toString(36).substring(2);
}

// Weak crypto
import * as crypto from "crypto";
function hashPassword(password: string): string {
    return crypto.createHash("md5").update(password).digest("hex");
}
