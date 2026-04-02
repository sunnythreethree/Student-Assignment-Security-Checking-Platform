// vulnerable_javascript.js — sample file for SAST Platform demo
//
// Submit this file via the UI (language: javascript) to produce known findings.
// Expected Semgrep results:
//   javascript.lang.security.audit.eval-detected        — eval injection
//   javascript.sequelize.security.audit.sequelize-injection — SQL injection via template literal
//   javascript.lang.security.audit.hardcoded-credentials — hardcoded secret

const express = require('express');
const { Sequelize } = require('sequelize');

const DB_PASSWORD = 'supersecret123';   // hardcoded credential

const app = express();
const sequelize = new Sequelize(`postgres://admin:${DB_PASSWORD}@localhost/mydb`);

// SQL injection via template literal — user input embedded directly in query
app.get('/user', async (req, res) => {
    const userId = req.query.id;
    const result = await sequelize.query(
        `SELECT * FROM users WHERE id = ${userId}`   // injection: userId not sanitized
    );
    res.json(result);
});

// eval injection — user-controlled code executed directly
app.get('/calc', (req, res) => {
    const expression = req.query.expr;
    const answer = eval(expression);    // arbitrary code execution
    res.send(`Result: ${answer}`);
});

app.listen(3000);
