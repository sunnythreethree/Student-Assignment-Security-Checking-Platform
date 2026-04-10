// vuln.go — intentionally vulnerable Go code for scanner testing.
// Expected: findings from Semgrep (command injection, SQL injection).
package main

import (
	"database/sql"
	"fmt"
	"os/exec"
)

// Command injection: user input passed directly to exec.Command
func runUserCommand(userInput string) {
	cmd := exec.Command("sh", "-c", userInput)
	cmd.Run()
}

// SQL injection via string concatenation
func getUser(db *sql.DB, username string) {
	query := fmt.Sprintf("SELECT * FROM users WHERE name = '%s'", username)
	db.Query(query)
}

// Hardcoded secret
const apiSecret = "hardcoded-go-secret-key-xyz"

func main() {
	fmt.Println("vulnerable go app")
}
